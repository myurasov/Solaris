_Rev. 1_

# Project type: ios-app <!-- omit in toc -->

- [Intended shape (`source/`)](#intended-shape-source)
- [Why it is a stub in v0](#why-it-is-a-stub-in-v0)
- [Notes](#notes)

Description-only stub for v0 - **not exercised yet**. Present so `create-project` can list it and so the
shape is documented for a future version.

## Intended shape (`source/`)

- An Xcode project (`*.xcodeproj` / `*.xcworkspace`), Swift + SwiftUI, organized by feature.
- Tests via XCTest.

## Why it is a stub in v0

iOS builds need Xcode toolchain integration and a different run/deploy story (simulator/device, signing)
than the rsync+ssh model. Solaris v0 validates `web-service` and `python-cli`; `ios-app` is documented but
its build/run workflow is deferred to a later version.

## Notes

If you pick this type now, expect to define the build/run/test commands by hand in
`<pack>/instructions.md`; Solaris will not assume an iOS toolchain.
