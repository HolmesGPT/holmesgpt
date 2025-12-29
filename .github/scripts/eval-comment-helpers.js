/**
 * Shared helper functions for eval workflow PR comments.
 * Used by multiple steps in .github/workflows/eval-regression.yaml
 */

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
 * @returns {string} Markdown table
 */
function renderParamsTable(p) {
  return `| Parameter | Value |\n|-----------|-------|\n` +
    `| **Triggered via** | ${p.trigger} |\n` +
    `| **Model** | \`${p.model}\` |\n` +
    `| **Markers** | \`${p.markers || 'all LLM tests'}\` |\n` +
    (p.filter ? `| **Filter (-k)** | \`${p.filter}\` |\n` : '') +
    `| **Iterations** | ${p.iterations} |\n` +
    (p.duration ? `| **Duration** | ${p.duration} |\n` : '') +
    `| **Workflow** | [View logs](${p.runUrl}) |\n`;
}

/**
 * Build comment body based on state
 * @param {Object} p - Parameters object
 * @param {Array<[boolean, string]>} progressSteps - Progress steps
 * @param {Object} extras - Extra options (icon, title, testPreview)
 * @returns {string} Markdown body
 */
function buildBody(p, progressSteps, extras = {}) {
  const progressText = renderProgress(progressSteps);

  let body = p.isManual
    ? `## ${extras.icon || '🚀'} ${extras.title || 'Manual Eval Running...'}\n\n` +
      renderParamsTable(p) + `\n**Progress:**\n${progressText}\n`
    : `## ${extras.icon || '⏳'} ${extras.title || 'HolmesGPT evals running...'}\n\n` +
      `Automatically triggered by ${p.trigger}\n\n` +
      `[View workflow logs](${p.runUrl})\n\n` +
      `**Progress:**\n${progressText}\n`;

  if (extras.testPreview) {
    body += `\n<details>\n<summary>📋 Evals to run</summary>\n\n\`\`\`\n${extras.testPreview}\n\`\`\`\n</details>\n`;
  }

  return body;
}

/**
 * Build re-run instructions footer for automatic runs
 * @param {Object} p - Parameters object with validMarkers, askHolmesEvals, investigateEvals
 * @param {Object} context - GitHub context object
 * @returns {string} Markdown footer
 */
function buildRerunFooter(p, context) {
  const workflowUrl = `https://github.com/${context.repo.owner}/${context.repo.repo}/actions/workflows/eval-regression.yaml`;
  return '\n---\n<details>\n<summary>🔄 <b>Re-run evals manually</b></summary>\n\n' +
    '> ⚠️ **Warning:** Manual re-runs have NO default markers and will run ALL LLM tests (~100+), which can take 1+ hours. ' +
    'Use `markers: regression` or `filter: test_name` to limit scope.\n\n' +
    '**Option 1: Comment on this PR** with `/eval`:\n\n' +
    '```\n/eval\nmarkers: regression\n```\n\n' +
    'Or with more options (one per line):\n\n' +
    '```\n/eval\nmodel: gpt-4o\nmarkers: regression\nfilter: 09_crashpod\niterations: 5\n```\n\n' +
    '| Option | Description |\n|--------|-------------|\n' +
    '| `model` | Model(s) to test (default: same as automatic runs) |\n' +
    '| `markers` | Pytest markers (**no default - runs all tests!**) |\n' +
    '| `filter` | Pytest -k filter |\n' +
    '| `iterations` | Number of runs, max 10 |\n\n' +
    `**Option 2: [Trigger via GitHub Actions UI](${workflowUrl})** → "Run workflow"\n</details>\n` +
    '\n<details>\n<summary>🏷️ <b>Valid markers</b></summary>\n\n' +
    (p.validMarkers || '_(No markers found)_') +
    '\n</details>\n' +
    '\n<details>\n<summary>📋 <b>Valid eval names (use with filter)</b></summary>\n\n' +
    '**test_ask_holmes:**\n' +
    (p.askHolmesEvals || '_(No evals found)_') +
    '\n\n**test_investigate:**\n' +
    (p.investigateEvals || '_(No evals found)_') +
    '\n</details>\n';
}

module.exports = {
  renderProgress,
  renderParamsTable,
  buildBody,
  buildRerunFooter
};
