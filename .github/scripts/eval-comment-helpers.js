/**
 * Shared helper functions for eval workflow PR comments.
 * Used by multiple steps in .github/workflows/eval-regression.yaml
 */

/**
 * Build params object from raw step outputs
 * Handles all type conversions and defaults in one place
 * @param {Object} raw - Raw outputs from GitHub Actions steps
 * @returns {Object} Normalized params object
 */
function buildParams(raw) {
  return {
    isManual: raw.is_manual === 'true',
    trigger: raw.trigger_source,
    model: raw.model || 'default',
    markers: raw.markers,
    filter: raw.filter,
    iterations: raw.iterations,
    branch: raw.branch || '',
    displayBranch: raw.display_branch || '',
    runUrl: raw.run_url,
    prNumber: raw.pr_number ? parseInt(raw.pr_number, 10) : null,
    commentId: raw.comment_id ? parseInt(raw.comment_id, 10) : null,
    testCount: raw.test_count || '0',
    testPreview: raw.test_preview || '',
    duration: raw.duration || 'N/A',
    validMarkers: raw.valid_markers || '',
    triggered_by: raw.triggered_by || ''
  };
}

/**
 * Render a progress checklist
 * @param {Array<[boolean, string]>} steps - Array of [completed, text] tuples
 * @returns {string} Markdown checklist
 */
function renderProgress(steps) {
  return steps.map(([done, text]) =>
    done ? `- [x] ${text}` : `- [ ] ${text}`
  ).join('\n');
}

/**
 * Render parameters table for manual runs
 * @param {Object} p - Parameters object
 * @param {Object} context - GitHub context object (optional, for rerun link)
 * @returns {string} Markdown table
 */
function renderParamsTable(p, context = null) {
  let workflowLinks = `[View logs](${p.runUrl})`;
  if (context) {
    const baseWorkflowUrl = `https://github.com/${context.repo.owner}/${context.repo.repo}/actions/workflows/eval-regression.yaml`;
    const rerunUrl = p.displayBranch ? `${baseWorkflowUrl}?ref=${encodeURIComponent(p.displayBranch)}` : baseWorkflowUrl;
    workflowLinks += ` \\| [Rerun](${rerunUrl})`;
  }
  return `| Parameter | Value |\n|-----------|-------|\n` +
    `| **Triggered via** | ${p.trigger} |\n` +
    (p.displayBranch ? `| **Branch** | \`${p.displayBranch}\` |\n` : '') +
    `| **Model** | \`${p.model}\` |\n` +
    `| **Markers** | \`${p.markers || 'all LLM tests'}\` |\n` +
    (p.filter ? `| **Filter (-k)** | \`${p.filter}\` |\n` : '') +
    `| **Iterations** | ${p.iterations} |\n` +
    (p.duration ? `| **Duration** | ${p.duration} |\n` : '') +
    `| **Workflow** | ${workflowLinks} |\n`;
}

/**
 * Build comment body based on state
 * @param {Object} p - Parameters object
 * @param {Array<[boolean, string]>} progressSteps - Progress steps (null to hide)
 * @param {Object} extras - Extra options (icon, title, testPreview, context)
 * @returns {string} Markdown body
 */
function buildBody(p, progressSteps, extras = {}) {
  let body = p.isManual
    ? `## ${extras.icon || '🚀'} ${extras.title || 'Manual Eval Running...'}\n\n` +
      renderParamsTable(p, extras.context)
    : `## ${extras.icon || '⏳'} ${extras.title || 'HolmesGPT evals running...'}\n\n` +
      `Automatically triggered by ${p.trigger}\n\n` +
      `[View workflow logs](${p.runUrl})\n`;

  // Only show progress if steps provided (null = hide for completed runs)
  if (progressSteps) {
    body += `\n**Progress:**\n${renderProgress(progressSteps)}\n`;
  }

  if (extras.testPreview) {
    body += `\n<details>\n<summary>📋 Evals to run</summary>\n\n\`\`\`\n${extras.testPreview}\n\`\`\`\n</details>\n`;
  }

  return body;
}

/**
 * Format comma-separated items as code-styled list
 * @param {string} items - Comma-separated items
 * @returns {string} Formatted items
 */
function formatAsCodes(items) {
  if (!items) return '_(loading...)_';
  return items.split(',').map(item => {
    const trimmed = item.trim();
    if (!trimmed) return '';
    return `\`${trimmed}\``;
  }).filter(Boolean).join(', ');
}

/**
 * Build re-run instructions footer for automatic runs
 * @param {Object} p - Parameters object with validMarkers
 * @param {Object} context - GitHub context object
 * @param {Object} options - Options (includeLegend: boolean)
 * @returns {string} Markdown footer
 */
function buildRerunFooter(p, context, options = {}) {
  const { includeLegend = false } = options;
  const repoFullName = `${context.repo.owner}/${context.repo.repo}`;
  const baseWorkflowUrl = `https://github.com/${repoFullName}/actions/workflows/eval-regression.yaml`;
  const workflowUrl = p.displayBranch ? `${baseWorkflowUrl}?ref=${encodeURIComponent(p.displayBranch)}` : baseWorkflowUrl;

  // Format markers as comma-separated code-styled names
  const markersFormatted = formatAsCodes(p.validMarkers);

  // gh CLI command to run workflow from PR branch
  const ghCommand = p.displayBranch
    ? `gh workflow run eval-regression.yaml --repo ${repoFullName} --ref ${p.displayBranch} -f markers=regression`
    : `gh workflow run eval-regression.yaml --repo ${repoFullName} -f markers=regression`;

  let footer = '';

  // Only show legend when results are displayed
  if (includeLegend) {
    footer += '\n<details>\n<summary>📖 <b>Legend</b></summary>\n\n' +
      '| Icon | Meaning |\n|------|--------|\n' +
      '| ✅ | The test was successful |\n' +
      '| ➖ | The test was skipped |\n' +
      '| ⚠️ | The test failed but is known to be flaky or known to fail |\n' +
      '| 🚧 | The test had a setup failure (not a code regression) |\n' +
      '| 🔧 | The test failed due to mock data issues (not a code regression) |\n' +
      '| 🚫 | The test was throttled by API rate limits/overload |\n' +
      '| ❌ | The test failed and should be fixed before merging the PR |\n' +
      '</details>\n';
  }

  footer += '\n<details>\n<summary>🔄 <b>Re-run evals manually</b></summary>\n\n' +
    '> ⚠️ **Warning:** `/eval` comments always run using the **workflow from master**, not from this PR branch. ' +
    'If you modified the GitHub Action (e.g., added secrets or env vars), those changes won\'t take effect.\n>\n' +
    '> **To test workflow changes**, use the GitHub CLI or [Actions UI](' + workflowUrl + ') instead:\n>\n' +
    '> ```\n> ' + ghCommand + '\n> ```\n\n' +
    '---\n\n' +
    '**Option 1: Comment on this PR** with `/eval`:\n\n' +
    '```\n/eval\nmarkers: regression\n```\n\n' +
    'Or with more options (one per line):\n\n' +
    '```\n/eval\nmodel: gpt-4o\nmarkers: regression\nfilter: 09_crashpod\niterations: 5\n```\n\n' +
    'Run evals on a different branch (e.g., master) for comparison:\n\n' +
    '```\n/eval\nbranch: master\nmarkers: regression\n```\n\n' +
    '| Option | Description |\n|--------|-------------|\n' +
    '| `model` | Model(s) to test (default: same as automatic runs) |\n' +
    '| `markers` | Pytest markers (**no default - runs all tests!**) |\n' +
    '| `filter` | Pytest -k filter (use `/list` to see valid eval names) |\n' +
    '| `iterations` | Number of runs, max 10 |\n' +
    '| `branch` | Run evals on a different branch (for cross-branch comparison) |\n\n' +
    '**Quick re-run:** Use `/last` to re-run the most recent `/eval` on this PR with the same parameters.\n\n' +
    `**Option 2: [Trigger via GitHub Actions UI](${workflowUrl})** → "Run workflow"\n</details>\n` +
    '\n<details>\n<summary>🏷️ <b>Valid markers</b></summary>\n\n' +
    markersFormatted +
    '\n</details>\n' +
    '\n---\n**Commands:** `/eval` · `/last` · `/list`\n';

  return footer;
}

module.exports = {
  buildParams,
  renderProgress,
  renderParamsTable,
  buildBody,
  buildRerunFooter
};
