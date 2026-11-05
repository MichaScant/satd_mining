==============================================================================
1. DOCUMENTING OWN DEBT — WITHOUT developer intervention (spontaneous)
==============================================================================

  [copilot/design] dotnet/runtime#122406
     "the async stub path and call the non-async variant (which ends up here)."

  [copilot/design] open-metadata/OpenMetadata#24234
     "Hardcode to TABLE entity since tests are only defined on tables and columns for now"

  [copilot/design] Azure/azure-sdk-for-js#36579
     "Live test run not supported on CI at the moment. Locally should work though."

  [copilot/design] microsoft/vscode#276538
     "TODO@deepak1556 workaround for https://github.com/microsoft/vscode/issues/250626"

  [copilot/design] Azure/azure-sdk-for-net#50564
     "quality is not supported for LTS or STS channel"

  [copilot/design] microsoft/vscode#261267
     "For now, we return all sessions and let MSAL handle the claims requirement during token acquisition"

  [copilot/design] microsoft/vscode#253008
     "- revisit this when Chat is available in serverless web"

  [copilot/design] dotnet/maui#33414
     "this stub will be replaced at compile time"

  [copilot/design] dotnet/sdk#51214
     "For now, only provide the diagnostic without an automatic fix"

  [copilot/design] dotnet/runtime#120906
     "Deserialization of tuples as objects is not supported"

  [copilot/design] microsoft/typespec#7793
     "TODO: Consider making it work with any number of paths. For now:"

  [devin/design] airbytehq/airbyte#68255
     "So for now just include the full stream object."

  [devin/design] airbytehq/airbyte#71048
     "The CartesianProductStreamSlicer warns that "Parent state handling is not supported""

  [copilot/design] dotnet/runtime#121337
     "For now, we only support empty baselines (version 0) which contain no data"

  [devin/design] airbytehq/airbyte#70951
     "placeholder.com/stories/username/some_id_value","

==============================================================================
2. DOCUMENTING OWN DEBT — WITH developer intervention (dev flags it first)
==============================================================================

  [copilot/design] microsoft/vscode#54363  (trigger: dev alluded to error)
     DEV:   "changes_requested"
     AGENT: "HACK: Currently this just matches leading line content, it should eventually check the"

  [copilot/design] open-metadata/OpenMetadata#54357  (trigger: dev alluded to error)
     DEV:   "@copilot unit tests are failing in this PR."
     AGENT: "If offline_access is not supported by Azure, just warn"

  [copilot/design] dotnet/runtime#86051  (trigger: dev alluded to error)
     DEV:   "In the reported issue, the input string is different. It does not end with , but (g => A). Please use the exact string from the bug report."
     AGENT: "IL stub's secret MethodDesc parameter (JitFlags::JIT_FLAG_PUBLISH_SECRET_PARAM)"

  [copilot/design] dotnet/roslyn#85760  (trigger: dev named debt)
     DEV:   "@copilot two tests are failing: Relational_10 and Pointer_Pattern_Equality. Investigate and upddate PR as appropriate."
     AGENT: "(1,14): error CS9345: The '!=' operator is not supported in a pattern. Use 'not' to represent a negated pattern."

  [copilot/design] dotnet/runtime#86689  (trigger: dev named debt)
     DEV:   "This is consistent with what I had thought might be going wrong in the code, but I did not try stepping into the code to verify."
     AGENT: "Just say it's all user time for now"

  [copilot/design] MudBlazor/MudBlazor#88158  (trigger: dev named debt)
     DEV:   "@copilot 1. SetColorInput_InvalidNoChange("#8qb829ff") - Error: "Expected string to be the same string, but they differ at index 1" - Appears during assertion comparing expected vs"
     AGENT: "TODO: Revisit this when the state of input components / validation improves, for now mimic old behavior"

  [copilot/design] dotnet/runtime#86465  (trigger: dev alluded to error)
     DEV:   "I'm seeing a failure with OpenSUSE 16.0. Looks like something very basic. Context: @wfurt"
     AGENT: "Data-loss conversion on IL stack merge point. We will need to restart compilation, but for now, emit a mov to continue processing, but update the stack data to "

  [copilot/design] microsoft/vscode#87499  (trigger: changes_requested)
     DEV:   "changes_requested"
     AGENT: "TODO@deepak1556 temporary until a real fix lands upstream"

  [copilot/design] microsoft/vscode#88001  (trigger: changes_requested)
     DEV:   "shouldn't change src/vs/workbench/services/preferences/browser/preferencesService.ts"
     AGENT: "Firefox pastes "LINE\n", so let's work around this quirk"

  [copilot/design] microsoft/vscode#87970  (trigger: changes_requested)
     DEV:   "changes_requested"
     AGENT: "Blur the background placeholder screenshot if the view is hidden due to an overlay."

  [copilot/design] dotnet/runtime#49070  (trigger: dev alluded to error)
     DEV:   "I was wondering about consistency so we can tell that all APIs work on all primitives, but seems like it's fine to remove FP, these apis aren't widely used judging by the grep.app "
     AGENT: "Floating-point types and floating-point backed enums are not supported."

  [copilot/design] Azure/azure-sdk-for-python#1118  (trigger: dev named debt)
     DEV:   "@bambriz Why did we remove these two positional arguments? This seems to be out of scope for this PR."
     AGENT: "Workaround. We might consider remove it in the future."

  [copilot/design] microsoft/vscode#54865  (trigger: changes_requested)
     DEV:   "Adopt this new Branch type in the git fig spec https://github.com/microsoft/vscode/blob/42701d5544f92f4611e9658f442d86f90b31fc57/extensions/terminal-suggest/src/completions/git.ts "
     AGENT: "TODO(jospicer): Fallback that should be removed/generalized when API stabilizes"

  [copilot/design] microsoft/vscode#87970  (trigger: changes_requested)
     DEV:   "changes_requested"
     AGENT: "Label (workspace name by default, placeholder on hover)"

  [copilot/design] microsoft/vscode#54857  (trigger: dev alluded to error)
     DEV:   "@copilot This seems completely wrong, we should be leveraging the tree view structure to be able to have a node that has the chat history as children. That way we can expand just h"
     AGENT: "Pass the alpha version of Playwright down... This is a hack since Playwright MCP"

==============================================================================
3. REVISITING OWN DEBT — WITHOUT developer intervention (spontaneous)
==============================================================================

  [devin] airbytehq/airbyte#71765
     "Fixed the changelog entry for version 1.0.5 - replaced the placeholder with the correct PR number"

  [claude] tldraw/tldraw#6743
     "Replace hardcoded text measurement with proper editor.textMeasure.measureText()"

  [copilot] OWASP-BLT/BLT#5027
     "Remove this duplicate import and use the existing one from line 6."

  [copilot] OWASP-BLT/BLT#5027
     "Fixed in f7317df - removed duplicate import and used existing urlencode from line 7"

  [copilot] OWASP-BLT/BLT#5064
     "Deleting duplicate user accounts without any backup or logging is destructive and can result in permanent data loss."

  [copilot] OWASP-BLT/BLT#5236
     "Consider either removing the duplicate entry in section 3.1 or clarifying that it's a cross-reference to section 1.1."

==============================================================================
4. REVISITING OWN DEBT — WITH developer intervention
==============================================================================

  [copilot] ArcadeData/arcadedb#2648  (dev alluded to error)
     "Removed both the duplicate try statement and its corresponding closing brace that was at the end of the finally block."

  [copilot] Azure/azure-sdk-for-js#34552  (dev named debt)
     "I've completely removed the stub approach and implemented a simpler solution."

  [copilot] Azure/azure-sdk-for-js#34946  (dev named debt)
     "Replaced hardcoded with variable reference to maintain consistency with the template variable system."

  [copilot] Azure/azure-sdk-tools#10842  (dev named debt)
     "I've removed the duplicate HashSet and now use just for both purposes (allowing when nested, flagging when at top level)."

  [copilot] Azure/azure-sdk-tools#11432  (dev named debt)
     "I've replaced the hardcoded Windows path separator with for cross-platform compatibility:"

  [copilot] Azure/azure-sdk-for-python#42195  (dev named debt)
     "Removed the duplicate claims checking logic from both and methods."

  [copilot] Azure/azure-sdk-for-net#50182  (dev named debt)
     "I've updated the Migration_Candidates.md file to use placeholder links instead of actual links."

  [copilot] Azure/azure-sdk-for-net#50610  (dev named debt)
     "Removed the duplicate file and updated both CI files to use the existing template at ."

  [copilot] Azure/azure-sdk-for-net#50564  (dev alluded to error)
     "Fixed the ambiguous call sites by removing the duplicate KeyVaultSetting method from the manual model factory."

  [copilot] Azure/azure-sdk-for-net#51345  (dev named debt)
     "Removed the completed TODO comment from the ResourceMetadata interface."

  [copilot] Azure/azure-sdk-for-net#51418  (dev named debt)
     "This eliminates the duplication and now we only have one hard-coded "CreateOrUpdate" string."

  [copilot] Azure/azure-sdk-for-net#51418  (dev named debt)
     "This is much cleaner and eliminates duplication."

  [copilot] Azure/azure-sdk-for-net#52229  (dev alluded to error)
     "Fixed the path separator issue by using to construct expected paths in the tests instead of hardcoded strings."

  [copilot] Homebrew/brew#20455  (dev named debt)
     "Removed the unnecessary comment that duplicated the debug message."

  [copilot] Homebrew/brew#20455  (dev named debt)
     "Removed the unnecessary comment that duplicated the debug message."