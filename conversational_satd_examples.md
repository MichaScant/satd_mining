# AI coding agents admitting technical debt in PR conversations

First-person admissions by genuine coding agents (Copilot / Devin) where the agent
acknowledges a **shortcut, limitation, or mistake it is leaving in the code** — i.e.
self-admitted technical debt, not a fix report. `…` = inline code stripped.
Source: `db/satd_mining.db` (`satd_instances` ⋈ `review_events`).

Note: clean admissions of *left* debt are relatively rare — most agent replies are fix
reports/status updates. These are the clearest cases from the genuine-coding-agent subset.

---

**Copilot — microsoft/PowerToys #40740**  *(the clearest case: hardcode + TODO, deferred)*

> You're absolutely right! ResourceLoaderInstance is not accessible from the Core.ViewModels
> project since it doesn't have a reference to Microsoft.CmdPal.UI. **I replaced it with a
> hardcoded string for now with a TODO comment to implement proper localization once the
> infrastructure is available.** Commit 3bc6be6

<https://github.com/microsoft/PowerToys/pull/40740>

---

**Copilot — dotnet/sdk #51609**  *(deliberate shortcut + acknowledged limitation)*

> Renamed … and placed after other CscOnly_AfterMSBuild tests. **I've kept it as a single test
> for now** rather than adding a variant with …, as the test already covers the key scenario
> and **has limitations in verification** (MSBuild restore recreates deleted packages). Let me
> know if you'd still like the … variation added. (30ca406)

<https://github.com/dotnet/sdk/pull/51609>

---

**Copilot — Azure/azure-sdk-for-net #54528**  *(incomplete work, deferred to follow-up)*

> Updated in commit 2113beb. I've simplified the tests to stop checking clients and instead
> directly assert information in the ArmProviderSchema: **5 more tests still need similar
> updates. They follow the same pattern and can be updated in a follow-up if needed.**

<https://github.com/Azure/azure-sdk-for-net/pull/54528>

---

**Copilot — RustPython/RustPython #6499**  *(knowingly leaves a test skipped)*

> I temporarily re-enabled the test locally and ran … 100 times on Linux; it passed 100/100 and
> I couldn't reproduce a flake here. The original flakiness appears to be macOS-specific
> (SecureTransport/system trust store sometimes accepts the self-signed localhost cert), **so
> the skip added in 43b7454 stays to avoid CI noise on that platform.**

<https://github.com/RustPython/RustPython/pull/6499>

---

**Devin — airbytehq/airbyte #62108**  *(acknowledges hardcoded assumptions remain elsewhere)*

> Based on my analysis of the connectors_qa codebase, I searched for hardcoded references to the
> OSS repository and found: … However, **you may want to consider checking other parts of the
> airbyte-ci tooling (outside of connectors_qa) for similar hardcoded OSS repository
> assumptions.**

<https://github.com/airbytehq/airbyte/pull/62108>
