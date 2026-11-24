# Publishing a release

## Prepare the GitHub repository

Create an empty GitHub repository after choosing the owner and visibility. From the checkout, initialize Git if needed, commit the tracked files and push `main`. Replace `OWNER` with the actual user or organization.

```sh
git init -b main
git add .
git commit -m "Add OpenAudioKit"
git remote add origin https://github.com/OWNER/OpenAudioKit.git
git push -u origin main
```

Enable private vulnerability reporting for the public repository. Wait for the Windows, Linux and macOS CI jobs before tagging. Add the actual repository and documentation URLs to the project metadata and the citation file before the first release.

## Prepare and validate distributions

Update the version in `pyproject.toml` and `CITATION.cff`, date the relevant changelog entry, and commit the changes. Build into a new or empty output directory so it only contains the intended release.

```sh
python -m build --outdir dist/0.2.0
python -m twine check --strict dist/0.2.0/*
python tools/checksums.py dist/0.2.0 0.2.0
```

Verify the name just before the first upload. As of 2026-11-24, the public PyPI JSON endpoint for `openaudiokit` returned HTTP 404. PyPI can reject names absent from its public index. [Project endpoint](https://pypi.org/pypi/openaudiokit/json) · [PyPI project names](https://docs.pypi.org/project-management/choosing-a-name/)

## Publish with Trusted Publishing

Create a pending publisher on PyPI for the chosen GitHub owner, repository, `release.yml` workflow and `pypi` environment. Configure the matching GitHub environment and restrict it to release tags and required reviewers where available. [PyPI Trusted Publishing setup](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)

Before the first tagged run, dispatch **Publish approved release** on the default branch and wait for it to finish. That run validates the build and saves artifacts in GitHub Actions. The PyPI job only runs on a tag. [GitHub workflow dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)

```sh
gh workflow run release.yml --ref main
```

Create and push the version tag, then dispatch the workflow against that tag. The workflow verifies the tag, runs tests and builds both artifacts. The protected environment controls the upload. [GitHub CLI workflow run](https://cli.github.com/manual/gh_workflow_run)

```sh
git tag -a v0.2.0 -m "OpenAudioKit 0.2.0"
git push origin v0.2.0
gh workflow run release.yml --ref v0.2.0
```

After the workflow succeeds, create the GitHub release for that tag using the changelog entry and optionally attach the validated wheel and source tarball. Confirm that a fresh environment can install the published version with `python -m pip install "openaudiokit[cli]==0.2.0"`.

## GitHub Pages and release assets

In the repository settings, choose **GitHub Actions** as the Pages source. Run
`gh workflow run pages.yml --ref main` after reviewing the deployment. Download
the `distributions` and `checksums` artifacts from the approved release run.

```sh
gh run download RUN_ID -n distributions -D release-assets
gh run download RUN_ID -n checksums -D release-assets
gh release create v0.2.0 release-assets/* --verify-tag --title "OpenAudioKit 0.2.0" --notes-file CHANGELOG.md
```

The Python archives leave Qt outside the package. The optional PyQt6 dependency
uses Riverbank's GPL/commercial terms. A Windows executable that bundles Qt and
PyQt requires a separate redistribution review, including the selected license,
notices, and corresponding-source obligations. Use the Python wheel as the core
distribution. [Riverbank licensing](https://www.riverbankcomputing.com/commercial/license-faq)

Before uploading, replace README asset links with HTTPS links under the actual
public repository or Pages site if they need to render on the PyPI project page.

Use the [release checklist](release-checklist.md) before dispatching a release.
