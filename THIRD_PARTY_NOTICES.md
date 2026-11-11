# Third-party rights and distribution

The root MIT licence covers entrant-owned original Interlock code and assets.
Ownership/release permission was attested by the entrant in
[the recorded rights statement](docs/HACKATHON_RELEASE_RIGHTS.md).
It does not relicense dependencies, fonts, provider marks or third-party material.

The default judge export contains the original application code and its locked
Next.js/React client dependencies, with Manrope and IBM Plex Mono fonts.
Full notices, including a conservative superset of Next.js compiled notices,
are in [PUBLIC_RUNTIME_NOTICES.txt](third_party/PUBLIC_RUNTIME_NOTICES.txt).
The build copies this file into its public notices directory.
Font origins and unmodified OFL texts remain in web/app/product/fonts.

The complete locked Node metadata inventory is
[node-license-inventory.json](third_party/node-license-inventory.json).
It includes build tools and dependencies of the separately installed local
Studio evaluation; it is not a claim that every listed package ships publicly.

AG Studio 3.1.0 and ag-studio-react 3.1.0 are commercial software.
The existing evaluation key expires December 25, 2026. Neither package nor its
transitive commercial or LGPL dependencies is relicensed under this project's
MIT licence. The default static export excludes Studio and the trial key.
Do not upload the local evaluation build. A Studio-enabled public evaluation
requires the sponsor's applicable judging-period arrangement.

Several installed Studio dependencies declare LGPL-3.0-or-later or combined
SPDX expressions; one installed dependency declares CC-BY-4.0. These retain their
own obligations. A future distribution that includes them must review applicable
licence texts, attribution, source availability and relinking/modification
requirements before release. Their presence in an npm install does not establish
permission to redistribute the local commercial evaluation bundle.

Backend Python dependencies retain the licences in their installed distributions
and version-pinned requirements.lock. No Python binary, PostgreSQL distribution,
provider SDK binary or private database is shipped in the public static demo.
The [locked Python inventory](third_party/python-license-inventory.json)
records the 36 installed distributions separately.

PayPal and sponsor names identify integrations; no endorsement is claimed.
No copied sponsor promotional images, copyrighted music or third-party video
footage is included. Product artwork is original CSS/SVG. Headless browser media
depicts the actual application and retains historical/synthetic classifications.

Generate the Node inventory and public notices using
node tools/release/notices.cjs after npm ci in web. This is an inventory and
notice-preservation process, not a complete legal review of every future
distribution configuration.
