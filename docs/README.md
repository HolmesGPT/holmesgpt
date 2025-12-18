# How to Build Documentation

This file explains how to build and serve the HolmesGPT documentation locally.

## Prerequisites

- Python 3.8+
- Poetry

## Installation

Install MkDocs and required dependencies using poetry:

```bash
poetry install --with=dev
```

## Building Documentation

### Development Server

To serve the documentation locally with live reload:

```bash
# From the repository root (serves on http://127.0.0.1:7000)
poetry run mkdocs serve --dev-addr=127.0.0.1:7000

# Or specify a different port
poetry run mkdocs serve --dev-addr=127.0.0.1:8001
```

The documentation will be available at `http://127.0.0.1:7000` (or the port you specified).

### Build Static Site

To build the static documentation site:

```bash
poetry run mkdocs build
```

This creates a `site/` directory with the built documentation.

### Strict Mode

To check for warnings and broken links:

```bash
poetry run mkdocs serve --strict
```

This will stop the build if there are any warnings.

## Configuration

The documentation is configured via `mkdocs.yml` in the repository root:

- **Source directory**: `docs/`
- **Theme**: Material for MkDocs
- **Navigation**: Defined in the `nav` section of mkdocs.yml

## File Organization

```
docs/
├── index.md                    # Homepage
├── installation/               # Installation guides
├── ai-providers/              # AI provider configuration
├── data-sources/              # Data source documentation
├── development/               # Development guides
├── reference/                 # Reference documentation
├── usage/                     # Usage guides
├── snippets/                  # Reusable content snippets
└── assets/                    # Images and static files
```

## Writing Documentation

### Markdown Guidelines

- Use ATX-style headers (`#`, `##`, `###`)
- Include relative links to other documentation pages
- Use code blocks with language specification
- Add alt text for images

### Navigation

To add a new page to the site navigation, edit the `nav` section in `mkdocs.yml`.

### Snippets

Reusable content is stored in `docs/snippets/` and can be included using:

```markdown
--8<-- "snippets/filename.md"
```

### Links

Use relative links for internal documentation:

```markdown
[Link text](../other-section/page.md)
```

## Excluding Files

Files and directories are excluded from the build if they:

- Start with underscore (`_`)
- Start with dot (`.`)
- Are listed in `.mkdocsignore` (if it exists)
- Are not included in the `nav` configuration

## Common Issues

### Broken Links
- Run `poetry run mkdocs serve --strict` to identify broken links
- Ensure all relative links use `.md` extension
- Check that linked files exist

### Missing Navigation
- Files not in `nav` will show warnings but won't be included in navigation
- Add important pages to the `nav` section in mkdocs.yml

### Asset Loading
- Place images in `docs/assets/`
- Use relative paths from the current page

## Deployment

The documentation is automatically built and deployed when changes are pushed to the main branch. The built site is served from GitHub Pages.

For manual deployment:

```bash
# Using poetry
poetry run mkdocs gh-deploy

# If you installed via pip directly
mkdocs gh-deploy
```

This builds and pushes the documentation to the `gh-pages` branch.
