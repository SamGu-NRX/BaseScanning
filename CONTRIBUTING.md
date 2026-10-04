# Contributing

## Branches and worktrees

Branch from `main` and open a pull request; nobody pushes to `main` directly. One person or agent writes to a branch at a time. Parallel work uses separate worktrees:

```sh
git worktree add ../house-scanning-<topic> -b <name>/<topic> origin/main
```

Clone with the landing page submodule, or fetch it later with `git submodule update --init sites/landing`.

## This repository is public

Never commit photos, video, scans or measurements of a real home, street addresses, meter or account numbers, or the materials Base gave the team. Base's materials stay in `private/`, real captures in `captures/` or `fixtures/real/`, and experiment outputs in `data/`. Git ignores all four. Test fixtures are synthetic.

## Pull requests

Keep a pull request focused. A larger change is fine when its parts belong together.

State what the old code did before describing the new behavior. Link the reviewed files where that helps. Show only changed UI behavior, list known gaps, and report commands that actually ran with their results.

The `size:*` label describes the effective diff. It is a review signal, not a merge gate. Do not set it by hand.

Greptile reviews new commits through [`.greptile/config.json`](.greptile/config.json). The dashboard's open-only trigger left follow-up commits unreviewed. This repository override adds push reviews without changing other repositories' settings. Greptile reads this setting from the PR's source branch, so older branches need the config too. For a branch without it, request a current review with `@greptileai review` after pushing. Use `@greptileai review this draft` for drafts.

## Checks

| Check | Runs on GitHub when | Local command |
| --- | --- | --- |
| Server checks | `server/` changes | `make server` |
| Web checks | `web/` changes | `make web` |
| iOS build | `ios/` changes outside Markdown, on non-draft pull requests | `make ios` |
| Makefile dispatch | `Makefile` or `tests/makefile.sh` changes | `bash tests/makefile.sh` |
| Sync label definitions | `.github/labels.json` changes on `main` | none |
| Label PR size | a pull request opens or updates | none |
| TestFlight | someone runs it from the Actions tab | none |

The iOS UI tests skip the every-state accessibility audit on pull requests, because it adds about 10 minutes and macOS runners are scarce. Add the `full-ui` label when a pull request changes screens or copy; pushes to `t3/ios-mvf` and `main` always run it.

The iOS check runs as three jobs, each under its own 90-minute cap: `screenStates` runs `ScreenStatesUITests`, the every-state audit included under `full-ui`; `photoProcessing` runs an unsigned Release build and `PhotoProcessingUITests` with its own audits; and `journey` runs the package tests, the Debug build and every other UI test class. `ios/Tools/ui-test-groups.py` holds the groups' test selectors; `python3 ios/Tools/ui-test-groups.py check` lists the UI tests from source and fails unless the groups run each exactly once. Run it after adding or renaming a UI test class. Each job keeps its result bundle as the artifact `ui-test-results-<group>`, with `SNAPSHOT.txt` saying whether the copy is complete; a job the cap cancelled can leave it incomplete.

`make check` runs the server, web and iOS suites, then `make scoring`, `make measure-lab`, `make evals`, `make recon` and `make meter-closeup` for each of those folders the branch has. They need uv, Node 24 with pnpm, and Xcode 26 or newer; each directory's README has details. No check is required by branch rules yet. Don't call one required until the rules require its status.

Keep workflows that run pull-request code away from production credentials and destructive external systems. A green CI run is evidence for the checks it ran, not proof that a capture works on a real house.

## Distribution

`.github/workflows/testflight.yml` archives one app, signs it and uploads it to TestFlight. It runs only when started by hand from the Actions tab. The build number is `<run number>.<attempt>`, such as `12.1`.

| App | Project | Bundle id |
| --- | --- | --- |
| House Scan (`house-scan`) | `ios/` | `<BUNDLE_ID_PREFIX>.housescan` |
| Measure Lab (`measure-lab`) | `experiments/measure-lab/` | `<BUNDLE_ID_PREFIX>.measurelab` |

### Before the first upload

App Store Connect rejects a build without an app icon. House Scan needs an `AppIcon` set with a 1024×1024 image in an asset catalog inside `ios/HouseScan/`, added by the client team. Measure Lab has one.

Neither `Info.plist` sets `ITSAppUsesNonExemptEncryption`, so each build waits under "Missing Compliance" until someone answers the encryption question on its TestFlight page. Setting the key to `false` removes that step for an app that uses only HTTPS and Apple's system encryption.

### One-time setup

1. **Create an App Store Connect API key.** In [App Store Connect](https://appstoreconnect.apple.com) > Users and Access > Integrations > App Store Connect API, create a team key with the **Admin** role. The export signs with a cloud-managed distribution certificate, which non-Admin keys cannot use. Download the `.p8` file (Apple offers it once) and note the Key ID and Issuer ID.

   The `.p8` and its base64 text are equally sensitive: either lets anyone sign and upload as the team. Never paste either into a chat, issue, pull request, screenshot, log or commit. If either was copied, clear the clipboard (`pbcopy < /dev/null`).
2. **Add the `testflight` environment.** In Settings > Environments, create `testflight` with two protection rules:

   - Required reviewers: Sam. Every run waits for his approval before GitHub releases any secret. Leave "Prevent self-review" off so he can approve runs he starts.
   - Deployment branches and tags: Selected branches, `main` only.

   An Admin key is acceptable here only because both rules hold: a run needs Sam's approval and code merged to `main`.

   Set the secrets and variables with the [GitHub CLI](https://cli.github.com), so the key never appears on screen. `gh secret set` without `--body` prompts without echoing.

   ```sh
   base64 -i AuthKey_<KEY_ID>.p8 | gh secret set ASC_KEY_P8 --env testflight --repo SamGu-NRX/house-scanning-master
   gh secret set ASC_KEY_ID --env testflight --repo SamGu-NRX/house-scanning-master
   gh secret set ASC_ISSUER_ID --env testflight --repo SamGu-NRX/house-scanning-master
   gh variable set APPLE_TEAM_ID --env testflight --repo SamGu-NRX/house-scanning-master --body <TEAM_ID>
   gh variable set BUNDLE_ID_PREFIX --env testflight --repo SamGu-NRX/house-scanning-master --body <PREFIX>
   rm AuthKey_<KEY_ID>.p8
   ```

   `APPLE_TEAM_ID` is the 10-character Team ID from developer.apple.com > Account > Membership details. `BUNDLE_ID_PREFIX` is a reverse-DNS prefix such as `com.yourname`.
3. **Register a device.** Archiving needs a development profile, which Apple does not issue to a team with no devices. Running either app on your iPhone from Xcode once registers it.
4. **Create the app records.** App Store Connect has no API for this. Register the App IDs `<BUNDLE_ID_PREFIX>.housescan` and `<BUNDLE_ID_PREFIX>.measurelab` under Certificates, Identifiers & Profiles > Identifiers. Then in App Store Connect > Apps > New App, create **House Scan** and **Measure Lab** for iOS with the matching bundle ids. Add a suffix if a name is taken.
5. **Add internal testers.** In each app's TestFlight tab, create an internal group, add team members who are App Store Connect users, and turn on automatic distribution.
6. **Run the workflow.** Actions > TestFlight > Run workflow, pick `main` and the app. Sam approves the pending deployment on the run page. The build appears in TestFlight after Apple processes it.

### What approving a run means

Approving a run also means Sam has reviewed the build inputs at the commit the run shows: `project.yml`, the generated project, xcconfig files and any local package manifest. That code builds while the key is on the runner.

The workflow limits what that code can do. "Check build inputs" runs before any key exists and fails the run on shell script build phases, build rules, scheme pre- or post-actions, remote Swift packages, package plugins, `Package.resolved`, nested projects and Swift compiler plugin flags. Each app may use only the in-repo packages named in the workflow's "Select project" step (`Geometry` for Measure Lab, `HouseScanKit` for House Scan), and their manifests may not declare dependencies, plugins, macros, binary targets or unsafe flags.

The key exists only inside the Archive step and the Export and upload step. Each decodes it into its own private directory under `RUNNER_TEMP` and deletes the directory when the step ends, fails or is cancelled. A final step removes any leftover. If the runner is killed before any of that runs, GitHub destroys the hosted runner after the job.

### Upkeep

Each run creates a new "Apple Development: Created via API" certificate. Revoke old ones under Certificates, Identifiers & Profiles > Certificates now and then. Uploaded builds are unaffected.
