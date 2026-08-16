'use strict';

// SECURITY: authorization for the /eval, /rerun and /list slash commands.
//
// These commands are handled by workflows that run in the BASE repository on the
// `issue_comment` event. That means a write-scoped GITHUB_TOKEN and the full
// repository secret set (every LLM provider key, datasource credential and
// Braintrust token) are present, and for /eval the job then checks out and
// *executes* the pull request's code. Deciding who is allowed to do that is the
// only thing standing between an outside contributor and the whole credential
// set, so it lives here in one trusted, unit-tested place rather than inline in
// workflow YAML.
//
// Two rules:
//
//   1. The actor must genuinely have write access to the base repository.
//      Never infer this from `comment.author_association`. That field describes a
//      *relationship*, not a permission level: GitHub returns COLLABORATOR for an
//      outside collaborator holding only `read` or `triage`. Trusting it hands
//      arbitrary code execution with secrets to anyone who has ever been added to
//      the repository in any capacity.
//
//   2. Releasing a *fork's* code to run with those secrets takes two people. The
//      author of the pull request cannot also be the one who marks it trusted,
//      because that gate would authorize nothing — the attacker would be standing
//      on both sides of it.
//
// Unit tests: tests/github_workflows/test_eval_authorization.py

// `permission` from the collaborator-permission API is the legacy four-value
// field: admin | write | read | none. The `maintain` role reports as `write`,
// and — the point of this whole module — `triage` reports as `read`. So this set
// means exactly "can push to the base repository".
const WRITE_PERMISSIONS = ['admin', 'write'];

// Horizontal whitespace only: `\s` would match newlines under the `m` flag and
// let the directive straddle lines.
const TRUSTED_DIRECTIVE_RE = /^[ \t]*trusted[ \t]*:[ \t]*(\S+)[ \t]*$/im;

const SHA_RE = /^[0-9a-f]{7,40}$/;

function hasWriteAccess(permission) {
  return WRITE_PERMISSIONS.includes(permission);
}

function sameLogin(a, b) {
  // GitHub logins are case-insensitive.
  return Boolean(a) && Boolean(b) && a.toLowerCase() === b.toLowerCase();
}

/**
 * Resolve the actor's effective permission on the repository.
 *
 * The API reports the *effective* level, so access granted through org base
 * permissions or team membership is included. Returns one of
 * admin | write | read | none. A 404 means "not a collaborator" and maps to
 * `none`; every other failure is re-thrown for the caller to fail closed on.
 */
async function getActorPermission({ github, owner, repo, username }) {
  try {
    const { data } = await github.rest.repos.getCollaboratorPermissionLevel({
      owner,
      repo,
      username,
    });
    return data.permission || 'none';
  } catch (error) {
    if (error && error.status === 404) {
      return 'none';
    }
    throw error;
  }
}

/**
 * Decide whether `actor` may run a slash command against this repository.
 *
 * Fails closed: if the permission cannot be established — rate limit, outage,
 * anything — the answer is no. Never throws; returns
 * { authorized, permission, reason }.
 */
async function authorizeCommand({ github, owner, repo, actor, command }) {
  if (!actor) {
    return {
      authorized: false,
      permission: 'unknown',
      reason: `Permission denied: could not determine who invoked ${command}.`,
    };
  }

  let permission;
  try {
    permission = await getActorPermission({ github, owner, repo, username: actor });
  } catch (error) {
    return {
      authorized: false,
      permission: 'unknown',
      reason:
        `Permission denied: could not verify @${actor}'s access to ${owner}/${repo} ` +
        `(${(error && error.message) || error}). Refusing to run ${command}.`,
    };
  }

  if (!hasWriteAccess(permission)) {
    return {
      authorized: false,
      permission,
      reason:
        `Permission denied: @${actor} has \`${permission}\` access to ${owner}/${repo}, ` +
        `and ${command} requires \`write\` or \`admin\`.`,
    };
  }

  return { authorized: true, permission, reason: '' };
}

/**
 * Extract the `trusted:` directive from a comment body.
 *
 * Returns null when absent, otherwise { value } with the raw token that followed
 * the colon. Validation of that token is authorizeForkRun's job.
 */
function parseTrustedDirective(body) {
  const match = (body || '').match(TRUSTED_DIRECTIVE_RE);
  return match ? { value: match[1] } : null;
}

function forkHelpText(command, headSha) {
  const shortSha = (headSha || '').slice(0, 12);
  return (
    `Running evals on a fork executes the fork's code with every repository secret ` +
    `(LLM provider keys, datasource credentials) and a write-scoped token, so it takes ` +
    `two people: a maintainer with write access who is **not** the PR author must review ` +
    `the diff and post the command.\n\n` +
    '```\n' +
    `${command}\n` +
    `trusted: ${shortSha || 'true'}\n` +
    `tags: regression\n` +
    '```\n\n' +
    (shortSha
      ? `Pinning the reviewed commit (\`trusted: ${shortSha}\`) is preferred — it fails ` +
        `instead of silently running a commit pushed after the review. \`trusted: true\` ` +
        `releases whatever the head commit is when the job starts.\n\n`
      : '') +
    `Maintainers can also use \`workflow_dispatch\` with \`is_pr_trusted: true\` from the Actions UI.`
  );
}

/**
 * Decide whether a fork's code may be released to run with repository secrets.
 *
 * `trusted` is the directive value already extracted from the *current* command
 * (`parseTrustedDirective`), or the literal 'true' for the workflow_dispatch
 * `is_pr_trusted` input. It must come from the comment being processed right now:
 * resolving it from an older /eval comment would let anyone replay a maintainer's
 * past approval with /rerun.
 *
 * Never throws; returns { authorized, code, reason, comment }, where `comment` is
 * markdown to post on the PR, or null when the denial should stay in the log.
 */
function authorizeForkRun({ actor, prAuthor, headRepoOwner, headSha, trusted, command }) {
  const label = command || '/eval';

  if (!trusted) {
    return {
      authorized: false,
      code: 'missing_trust',
      reason: `Fork PRs require an explicit \`trusted:\` confirmation to run ${label}.`,
      comment: `⚠️ \`${label}\` on a fork PR needs explicit trust confirmation.\n\n${forkHelpText(label, headSha)}`,
    };
  }

  // Rule 2. Both checks matter: prAuthor is who opened the PR, headRepoOwner is
  // who can push new commits to the branch being run. Either one approving their
  // own code makes the gate self-service.
  if (!prAuthor) {
    return {
      authorized: false,
      code: 'unknown_author',
      reason: `Refusing to run ${label}: could not determine the author of this pull request.`,
      comment: null,
    };
  }

  if (sameLogin(actor, prAuthor)) {
    return {
      authorized: false,
      code: 'self_approval',
      reason:
        `Permission denied: @${actor} opened this pull request and cannot also authorize ` +
        `its code to run with repository secrets.`,
      comment:
        `❌ @${actor} opened this pull request, so they cannot also mark it \`trusted\`.\n\n` +
        `${forkHelpText(label, headSha)}\n\n` +
        `If you have write access and this is your own branch, push it to the base ` +
        `repository instead of a fork — same-repo PRs do not need a trust confirmation.`,
    };
  }

  if (sameLogin(actor, headRepoOwner)) {
    return {
      authorized: false,
      code: 'self_approval',
      reason:
        `Permission denied: @${actor} owns the fork this pull request runs from and can push ` +
        `to it, so they cannot authorize it to run with repository secrets.`,
      comment:
        `❌ @${actor} owns the head repository for this pull request and can push new commits ` +
        `to it, so they cannot also mark it \`trusted\`.\n\n${forkHelpText(label, headSha)}`,
    };
  }

  // `trusted: <sha>` pins the review to a specific commit, closing the window
  // between "maintainer reviewed the diff" and "job resolves the head commit".
  const value = String(trusted).trim().toLowerCase();
  if (value !== 'true') {
    if (!SHA_RE.test(value)) {
      return {
        authorized: false,
        code: 'invalid_trust',
        reason: `Invalid \`trusted:\` value — expected \`true\` or the reviewed commit SHA, got \`${trusted}\`.`,
        comment:
          `❌ Invalid \`trusted:\` value \`${trusted}\`. Use \`trusted: true\`, or pin the commit ` +
          `you reviewed with \`trusted: <sha>\`.\n\n${forkHelpText(label, headSha)}`,
      };
    }
    if (!headSha || !headSha.toLowerCase().startsWith(value)) {
      return {
        authorized: false,
        code: 'stale_sha',
        reason:
          `Refusing to run ${label}: \`trusted: ${trusted}\` does not match the current head ` +
          `commit ${headSha || '<unknown>'}. The branch moved after it was reviewed.`,
        comment:
          `❌ \`trusted: ${trusted}\` does not match the current head commit \`${(headSha || '').slice(0, 12)}\`.\n\n` +
          `The branch was pushed to after the review. Re-review the new commits and post ` +
          `\`${label}\` again with the updated SHA.`,
      };
    }
  }

  return {
    authorized: true,
    code: 'ok',
    reason: '',
    comment: null,
    pinnedSha: value === 'true' ? null : value,
  };
}

module.exports = {
  WRITE_PERMISSIONS,
  hasWriteAccess,
  getActorPermission,
  authorizeCommand,
  parseTrustedDirective,
  authorizeForkRun,
};
