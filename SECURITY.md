# Security policy

## Supported versions

Security fixes target the latest version on the `main` branch and the latest published release. Older releases may not receive fixes.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through [GitHub Security Advisories](https://github.com/KiidxAtlas/simple-stipple/security/advisories/new). Do not open a public issue for an unpatched vulnerability.

Include:

- A concise description of the impact.
- The affected version, platform, and installation type.
- Reproduction steps or a minimal sanitized file, if safe to share.
- Any suggested mitigation.

Do not include passwords, access tokens, private designs, customer data, or other secrets. If a file is needed, describe the smallest safe fixture first.

You should receive an acknowledgement through GitHub. Please allow time for validation, remediation, and coordinated disclosure before publishing details.

## Scope notes

Simple Stipple reads user-provided vector and image files, writes exported geometry, checks GitHub releases when update checking is enabled, and can interact with a configured local Git repository. Reports involving malformed-file handling, unsafe path handling, update verification, credential exposure, or unintended network access are especially useful.
