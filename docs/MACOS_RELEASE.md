# macOS release setup

The macOS release job publishes a Gatekeeper-compatible DMG only when the
repository has Apple signing and notarization credentials. Configure these
GitHub Actions repository secrets before pushing a `v*` tag:

| Secret | Value |
| --- | --- |
| `MACOS_CERTIFICATE_BASE64` | Base64 contents of a Developer ID Application `.p12` certificate |
| `MACOS_CERTIFICATE_PASSWORD` | Password used when exporting the `.p12` |
| `MACOS_SIGNING_IDENTITY` | Exact `Developer ID Application: Name (TEAMID)` identity |
| `APPLE_ID` | Apple Developer account email |
| `APPLE_APP_PASSWORD` | App-specific password generated at appleid.apple.com |
| `APPLE_TEAM_ID` | 10-character Apple Developer Team ID |

## Export the certificate

In Keychain Access, export the **Developer ID Application** certificate as a
`.p12` file. Convert it to one line for the secret value:

```bash
base64 -i DeveloperIDApplication.p12 | tr -d '\n'
```

The signing identity can be found with:

```bash
security find-identity -v -p codesigning
```

Create the app-specific password at <https://appleid.apple.com> and use the
Team ID shown in the Apple Developer account membership page.

The workflow intentionally fails tagged releases when these values are
missing. That prevents publishing a DMG that macOS will classify as an
unverified developer artifact.
