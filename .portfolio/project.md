---
# kgu.one builds this project's page from this file (https://kgu.one/projects/basescanning).
# When a change alters what the project does, its results, awards, stack or links,
# update this file in the same change. Rules:
# - Facts only, each one backed by this repo, the resume or a public source.
# - No em dashes and no middle dots.
# - line: at most 120 characters, ending in a period. What someone does or gets,
#   then one mechanism. No adjectives.
# - The body opens with one paragraph of 50 to 80 words, first person: what it is,
#   who used it, the hard part, one fact. The site uses it as the summary.
# - The rest of the body is the full write-up, in plain Markdown (## and ###
#   headings, lists, emphasis, inline code, https links), at most 1,500 words.
title: BaseScanning
kind: project
date: 2026-09
line: Splat! Walk the wall by your meter with an iPhone, and a home battery lands in AR where it fits.
award: 1st, Most Commercializable, Base x AITX
badge: 1st
stack: [Swift, ARKit]
links:
  - label: Site
    href: https://house-scanning.vercel.app/
  - label: Code
    href: https://github.com/SamGu-NRX/BaseScanning
---

Base Power decides where a home battery can go from photos the homeowner sends in, and a photo can’t show what sits outside its frame. BaseScanning makes it one walk. You scan the wall by your meter with an iPhone, our server rebuilds that wall in 3D and checks it against every placement rule, and the answer comes back in AR. Four of us built it at the Base x AITX Talent Hackathon and won first place for Most Commercializable.

## What one walk records

One walk gives the server three readings. Seen: a close-up of the meter, then the wall and ground to the corner. Measured: the window, marked by two corners, 3 ft 3 in by 3 ft 7 in. Placed: a possible spot 12 ft from the meter, for an installer to check.

## What leaves the phone, and what comes back

The iPhone tracks the wall and what it has seen while you walk and mark it, and sends photos, poses and marks for the whole walk in one capture packet. Written rules, not a model, check the wall, and each check passes, fails or stays unsure. The answer is a spot, or one more view: an unsure check asks for a better view, and the app asks you for it. A spot shows up on your wall in AR, and an installer reviews every result.

## Unseen means unsure

The rule we cared about most is about what the phone didn’t see. A stretch of wall nobody filmed might be bare, or it might have a gas meter on it, and the server has no way to tell. So it marks that stretch unknown and never passes a spot that depends on it. The app asks for that view instead. A blank on the map is never treated as empty ground.

Measurements get the same caution. The phone tracks its position by adding up its own movements, so error piles up the farther you walk from the meter, and every measurement carries a margin. Take the 3 ft rule for gas meters. At 4.5 ft, give or take 0.8 ft, the check passes. At 3.4 ft with the same margin it could go either way, so it comes back unsure, and a person or a better view settles it.

The machine learning models build the wall and name what’s on it, but they never decide whether a battery fits. A deterministic check against a separate rules file does that. The server slides the battery’s outline along every stretch of wall the phone saw, 2 inches at a time, and runs 13 checks at each stop, among them the meter’s working space, gas and AC clearances and the cable run. Each check passes, fails or stays unsure, which the server’s contract calls `pass`, `reject` and `manual_review`, and anything it can’t settle goes to a person.

## My part

I built the iPhone app and the capture packet, the file the phone sends to the server, working with AI agents. ARKit anchors the meter, Vision reads its number, Metal hazes over the parts of the wall the camera hasn’t covered, and a RealityKit raycast places each tap. LiDAR helps when the phone has it, but the app doesn’t need it. Everything ends up in one `scene.json`. When the server finds a spot, RealityKit pins the battery to the real wall through the camera.

ARKit gives the app four layers: camera and pose on every iPhone, LiDAR depth and a classified mesh (wall, floor, door, window) on iPhones that have LiDAR, and planes with gravity, so +y points straight up. HouseScanKit puts every mark at s, feet along the wall from the meter. The wall, your marks, what the walk saw and each keyframe’s pose go into `scene.json`, one small file.

Hunter Carver built the 3D model and the rule checks. Aiden Johnston ran field tests on a real iPhone and fixed what they turned up. Shrey Suri wrote the README and drew its diagrams.

## What doesn’t work yet

- Most of the system still lives in open pull requests, guided capture and the rules engine included.
- Our first run on a real phone placed nothing. The app put both ends of the wall at the meter, which left the server a wall with no length to search.
- Our best tracking result came from an iPhone 14 Pro Max, which has LiDAR. An iPhone 6s drifted two to three times past what the server allows.
- Reading the meter number is only half solved. A nameplate carries several numbers, and the reader’s top three guesses held the right meter number on 27 of 34 held-out photos. For now the app shows those three candidates and the homeowner taps the right one.
- The app’s first coverage map checked only range, angle and framing, and claimed 1.1 ft of wall that no photo saw. Tested against depth, the recon worker now claims at most about 0.46 ft.
- LiDAR reaches about 5 m, so capture has to stand within about 4 m of the wall.
- The pool and driveway distances are placeholders, because we couldn’t find public values.
