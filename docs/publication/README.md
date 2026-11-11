# Publication audit

This directory is packaging metadata for the disclosed public source snapshot.
It does not replace or rewrite the original release history.

- [Source byte manifest](source-manifest.json): original source commit, exported
  hashes and the cross-platform packaging attribute exception.
- [Withheld material](omissions.json): internal operational reports and
  unapproved commercial-evaluation media remain preserved privately.
- [Final local verification](validation.json): actual focused and separate
  pre-publication checkout results. GitHub Actions is not configured; no CI
  success is claimed. The owner explicitly chose source publication without
  Actions after GitHub rejected workflow creation for missing OAuth scope.
- [Dependency review](dependency-review.json): the complete install reported
  **one high and nine moderate development-tooling findings**. The separate
  production audit reported zero. This is not a vulnerability-free dependency
  claim or permission to host the full private application.

The high finding is Zod 4.6.5 via the development-only React Hooks ESLint plugin.
The moderate chain is minimatch 10.2.6 through ESLint/TypeScript-ESLint. These
packages are not included in the standalone static application's runtime.
Local linting operates on repository-controlled files; no public lint or
validation API is exposed.
Dependency remediation remains maintenance work and requires lockfile changes
and regression validation; no automatic audit fix/downgrade was performed.

Advisories: [Zod](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5),
[minimatch](https://github.com/advisories/GHSA-g3hv-75wg-m249).

Gitleaks and known-private-value comparisons inspect the exact public Git object
set; they cannot prove that every unknown or rotated secret is absent. Fonts
retain their unmodified notices. MIT applies to original project code only.
The Studio evaluation key, proprietary runtime, screenshots and videos are not
distributed. Public Studio/media use still requires applicable permission.
