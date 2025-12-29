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
    runUrl: raw.run_url,
    prNumber: raw.pr_number ? parseInt(raw.pr_number, 10) : null,
    commentId: raw.comment_id ? parseInt(raw.comment_id, 10) : null,
    testCount: raw.test_count || '0',
    testPreview: raw.test_preview || '',
    duration: raw.duration || 'N/A',
    validMarkers: raw.valid_markers || '',
    askHolmesEvals: raw.ask_holmes_evals || '',
    investigateEvals: raw.investigate_evals || ''
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
 * @param {Array<[boolean, string]>} progressSteps - Progress steps (null to hide)
 * @param {Object} extras - Extra options (icon, title, testPreview)
 * @returns {string} Markdown body
 */
function buildBody(p, progressSteps, extras = {}) {
  let body = p.isManual
    ? `## ${extras.icon || '🚀'} ${extras.title || 'Manual Eval Running...'}\n\n` +
      renderParamsTable(p)
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
 * Build re-run instructions footer for automatic runs
 * @param {Object} p - Parameters object with validMarkers, askHolmesEvals, investigateEvals
 * @param {Object} context - GitHub context object
 * @returns {string} Markdown footer
 */
function buildRerunFooter(p, context) {
  const workflowUrl = `https://github.com/${context.repo.owner}/${context.repo.repo}/actions/workflows/eval-regression.yaml`;
  return '\n---\n' +
    '<details>\n<summary>📖 <b>Legend</b></summary>\n\n' +
    '| Icon | Meaning |\n|------|--------|\n' +
    '| ✅ | The test was successful |\n' +
    '| ➖ | The test was skipped |\n' +
    '| ⚠️ | The test failed but is known to be flaky or known to fail |\n' +
    '| 🚧 | The test had a setup failure (not a code regression) |\n' +
    '| 🔧 | The test failed due to mock data issues (not a code regression) |\n' +
    '| 🚫 | The test was throttled by API rate limits/overload |\n' +
    '| ❌ | The test failed and should be fixed before merging the PR |\n' +
    '</details>\n' +
    '\n<details>\n<summary>🔄 <b>Re-run evals manually</b></summary>\n\n' +
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
    (p.validMarkers || '_(Collecting from pyproject.toml...)_') +
    '\n</details>\n' +
    '\n<details>\n<summary>📋 <b>Valid eval names (use with filter)</b></summary>\n\n' +
    '**test_ask_holmes:**\n' +
    (p.askHolmesEvals || '_(Collecting from tests/llm/fixtures/...)_') +
    '\n\n**test_investigate:**\n' +
    (p.investigateEvals || '_(Collecting from tests/llm/fixtures/...)_') +
    '\n</details>\n';
}

module.exports = {
  buildParams,
  renderProgress,
  renderParamsTable,
  buildBody,
  buildRerunFooter
};
