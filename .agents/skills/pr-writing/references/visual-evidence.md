# Visual evidence

Decide which behavior changed before you capture anything. Capture the real route or component with the browser, simulator or desktop tools the host supports. Never redraw an interface as a stand-in for a screenshot. A design prototype shows the prototype, not the native app or the deployed product.

## Capture a fair comparison

Note the commit for the before and the after. Keep the device or viewport, route, data and state the same in both. For a bug, trigger the same condition on both versions. For a new feature, show the new flow and label it as new. If the old build won't run, label the missing or historical reference. Never fake a baseline.

Capture the base in a separate worktree or from an existing build, so you don't disturb whoever is working in the main checkout. Use approved test data. Say when fixture data limits a claim. A success screen doesn't prove the backend saved anything. If the capture procedure will be reused, commit it to the repository with the route, fixture and build commands that worked.

Put Before and After side by side at the same readable size. Label each and caption what changed in behavior. Crop both the same way, and keep enough around the change to judge it. Avoid tall comparison boards with blank gaps, and galleries of screens that didn't change. Keep the original captures when you build a comparison.

Record motion at normal speed, showing the trigger, the transition and the settled result. Trim idle time, but never cut a failure or change the timing under review. Use H.264 MP4 when the host plays it. Before uploading, watch the encoded file for dropped frames, unreadable text and wrong orientation.

## Publish and verify

Attach inline evidence through GitHub's repository attachments, especially in a private repository. Keep sensitive captures off anonymous public image hosts. A private raw-file URL or a local path won't render inline.

Use the GitHub CLI's attachment support if the installed version has it. Check `gh pr create --help` for `--attach` first. Otherwise use GitHub's attachment UI while signed in, or an uploader the repository already trusts. Don't carry one app's authentication workaround into another project. If a local tool composes the before-and-after image, check where it writes its output before you run it, so it doesn't upload captures somewhere else.

With CLI attachment support, reference the local files in the Markdown body and pass each file with `--attach` next to `--body-file`. The CLI swaps those references for uploaded URLs. If an upload fails partway, look at the returned PR and its body before retrying. The CLI can exit nonzero after it has already created the PR. Fix that PR instead of opening another.

Show a pair in a small Markdown table. Give each image alt text that says what it shows. Put a video in its own paragraph so GitHub renders it as a player. Keep the original attachment URLs when you edit an existing body.

Open the PR's rendered page. Check that both images load at a useful size, each label matches its commit, and the video plays. If you can't publish or view the PR, hand back the draft and the evidence paths, and name the step still left.

GitHub documents [attachment access and supported formats](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files) and [CLI attachment support](https://docs.github.com/en/github-cli/github-cli/attaching-files-with-github-cli).
