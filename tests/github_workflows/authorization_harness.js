#!/usr/bin/env node
'use strict';

// Test harness for .github/scripts/eval-authorization.js.
//
// The module runs inside actions/github-script in CI, so it is exercised here the
// same way: with a stubbed octokit client. Invoked as
//
//   node authorization_harness.js '<json request>'
//
// and prints a JSON response on stdout. Request shape:
//
//   {
//     "fn": "authorizeCommand" | "authorizeForkRun" | "parseTrustedDirective",
//     "args": { ... },                  // passed to the function
//     "permissionApi": {                // stubs getCollaboratorPermissionLevel
//       "permission": "write"           //   -> resolves with this permission
//       | "status": 404                 //   -> rejects (not a collaborator)
//       | "status": 500, "message": ""  //   -> rejects (API failure)
//     }
//   }
//
// The response is { "result": <return value>, "calls": [<api calls made>] }.

const path = require('path');

const MODULE_PATH = path.resolve(
  __dirname,
  '..',
  '..',
  '.github',
  'scripts',
  'eval-authorization.js',
);
const authz = require(MODULE_PATH);

function makeGithub(spec, calls) {
  return {
    rest: {
      repos: {
        getCollaboratorPermissionLevel: async (params) => {
          calls.push(params);
          if (!spec) {
            throw new Error('permissionApi not configured for this request');
          }
          if (spec.status) {
            const error = new Error(spec.message || `HTTP ${spec.status}`);
            error.status = spec.status;
            throw error;
          }
          return { data: { permission: spec.permission, role_name: spec.role_name } };
        },
      },
    },
  };
}

async function main() {
  const request = JSON.parse(process.argv[2]);
  const calls = [];
  const args = Object.assign({}, request.args);

  let result;
  switch (request.fn) {
    case 'authorizeCommand':
      args.github = makeGithub(request.permissionApi, calls);
      result = await authz.authorizeCommand(args);
      break;
    case 'authorizeForkRun':
      result = authz.authorizeForkRun(args);
      break;
    case 'parseTrustedDirective':
      result = authz.parseTrustedDirective(args.body);
      break;
    case 'hasWriteAccess':
      result = authz.hasWriteAccess(args.permission);
      break;
    default:
      throw new Error(`Unknown fn: ${request.fn}`);
  }

  process.stdout.write(JSON.stringify({ result, calls }));
}

main().catch((error) => {
  process.stderr.write(String((error && error.stack) || error));
  process.exit(1);
});
