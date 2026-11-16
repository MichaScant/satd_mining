================================================================================
A. CASE STUDIES — debt → developer conversation → the fix
================================================================================

--------------------------------------------------------------------------------
CASE 1: [copilot] dotnet/runtime  (PR #123294 → fixed in #129073)
circumstance: dev named debt   |   AI file-edits before fix: 2  (near the debt: 2)

  ① DEBT THE AI INTRODUCED  —  src/coreclr/jit/codegencommon.cpp
     comment: "ifndef TARGET_WASM // TODO-WASM: temporary; un-undefine as needed."
     code:
    @@ -5378,6 +5385,7 @@ void CodeGen::genFnProlog()
         }
     #endif // TARGET_ARM
     
    +#ifndef TARGET_WASM // TODO-WASM: temporary; un-undefine as needed.
         //
         // Zero out the frame as needed
         //

  ② DEVELOPER CONVERSATION (introducing PR #123294):
  AI   Copilot: - [x] Analyze the issue: for and returns incorrect values for subnormal numbers because only has overloads for / / , so the from is implicitly widened to , adding 16 extra leading zeros - [x] Fix to use which handles the
  DEV  stephentoub: @copilot, instead of adjusting the value at the call site, please instead add an internal overload of BitOperations.LeadingZeroCount that accepts a ushort.
  AI   Copilot: Done in 45b64f11. Added internal overloads for both and that handle the widening adjustment. Also updated the existing implementations in , , , , and to use these new overloads.
  DEV  tannergooding: @copilot This should just be using instead, which already does this and is why such an overload was never added.
  DEV  stephentoub: > and is why such an overload was never added Seems we should rethink that given the bugs this leads to?
  DEV  tannergooding: We can't, it becomes a silent behavioral breaking change due to how overload resolution works. It's the same reason we originally defined instead of just adding overloads to and ultimately landed on exposing all the APIs
  AI   Copilot: Done in 361521a3. Reverted the changes and updated and to use directly.
  DEV  stephentoub: > it becomes a silent behavioral breaking change due to how overload resolution works It does, but it's also likely a silent bug. If we're getting it wrong as we did here, others will as well. If we're not going to recon

  ③ THE FIX (PR #129073  —  src/coreclr/jit/codegencommon.cpp):
    @@ -5569,13 +5621,13 @@ void CodeGen::genFnProlog()
         }
     #endif // TARGET_ARM
     
    -#ifndef TARGET_WASM // TODO-WASM: temporary; un-undefine as needed.
         //
         // Zero out the frame as needed
         //
     
         genZeroInitFrame(untrLclHi, untrLclLo, initReg, &initRegZeroed);
     
    +#ifndef TARGET_WASM // TODO-WASM: enable as needed.
    …

  ④ CONVERSATION LEADING TO THE FIX (PR #129073):

--------------------------------------------------------------------------------
CASE 2: [copilot] serverpod/serverpod  (PR #4085 → fixed in #5090)
circumstance: dev named debt   |   AI file-edits before fix: 2  (near the debt: 2)

  ① DEBT THE AI INTRODUCED  —  packages/serverpod/lib/src/server/serverpod.dart
     comment: "Remove this when we have a better way to handle this."
     code:
    @@ -482,10 +482,11 @@ class Serverpod {
             databaseConfiguration,
           );
     
    -      // TODO(#4105): Remove this when we have a better way to handle this.
    -      // Tracked by issue: https://github.com/serverpod/serverpod/issues/2421
    -      // This is required because other operations in Serverpod assumes that the
    -      // database is connected when the Serverpod is created
    +      // TODO(https://github.com/serverpod/serverpod/issues/4105):
    +      // TODO(https://github.com/serverpod/serverpod/issues/2421):
    +      // Remove this when we have a better way to handle this.
    +      // This is required because other operations in Serverpod assumes that
    …

  ② DEVELOPER CONVERSATION (introducing PR #4085):
  AI   Copilot: - [x] Analyze all TODO markers in the codebase (47 found) - [x] Categorize TODOs by type and priority - [x] Fix deprecation-related TODOs for 3.0.0 release - [x] Remove unused parameter from function (breaking change) - 
  DEV  nielsenko: @copilot Problems with the current PR add a suggestion for an issue description to each.
  AI   Copilot: > @copilot > Problems with the current PR > - Don't touch generated code. ... Addressed all feedback in commit c72806b: 1. **Generated code**: Confirmed - no generated files were modified 2. **TODOs converted to comments
  DEV  nielsenko: @copilot Update "issues" relating to and to explicit defer to these classes from the package.
  AI   Copilot: > @copilot > > Update "issues" relating to and to explicit defer to these classes from the package. Updated in commit 27d45dd. Both issue descriptions now explicitly reference the package:
  DEV  nielsenko: @copilot Go ahead with the plan and create actual github issues. Make sure to update the TODOs in the code to point to these issues.
  AI   Copilot: > @copilot Go ahead with the plan and create actual github issues. Make sure to update the TODOs in the code to point to these issues. I don't have the capability to create GitHub issues - my available tools only allow r
  DEV  nielsenko: @coderabbit Create the issues described as missing in https://github.com/serverpod/serverpod/pull/4085/files#diff-3fd074d2e55c9a15ad75f8635fe4849c533918b430e0e705a35a116dbfa7c93a.

  ③ THE FIX (PR #5090  —  packages/serverpod/lib/src/server/serverpod.dart):
    @@ -595,23 +595,11 @@ class Serverpod {
         if (Features.enableDatabase && databaseConfiguration != null) {
           final databaseDialect = databaseConfiguration.dialect;
           final databaseProvider = DatabaseProvider.forDialect(databaseDialect);
    -      final databasePoolManager = databaseProvider.createPoolManager(
    +      _databasePoolManager = databaseProvider.createPoolManager(
             serializationManager,
             runtimeParametersBuilder,
             databaseConfiguration,
           );
    -
    -      // ISSUE(https://github.com/serverpod/serverpod/issues/2421):
    …

  ④ CONVERSATION LEADING TO THE FIX (PR #5090):

--------------------------------------------------------------------------------
CASE 3: [copilot] dotnet/maui  (PR #30226 → fixed in #38782)
circumstance: dev alluded to error   |   AI file-edits before fix: 1  (near the debt: 1)

  ① DEBT THE AI INTRODUCED  —  src/TestUtils/src/Microsoft.Maui.IntegrationTests/ResizetizerTests.cs
     comment: "net9.0-xxx"
     code:
    @@ -11,17 +11,25 @@ public class ResizetizerTests : BaseBuildTest
     		</svg>
     		""";
     
    -	[Test]
    +	[Fact]
     	// windows unpackaged/exe
    -	[TestCase("maui", "classlib", true)] // net9.0
    -	[TestCase("maui", "mauilib", true)] // net9.0-xxx
    -	[TestCase("maui-blazor", "classlib", true)] // net9.0
    -	[TestCase("maui-blazor", "mauilib", true)] // net9.0-xxx
    +	[Theory]
    …

  ② DEVELOPER CONVERSATION (introducing PR #30226):
  AI   Copilot: > [!NOTE] > Are you waiting for the changes in this PR to be merged? > It would be very helpful if you could [test the resulting artifacts](https://github.com/dotnet/maui/wiki/Testing-PR-Builds) from this PR and let us k
  DEV  mattleibow: Please format the code using
  DEV  mattleibow: Please finish the work. You said:
  AI   Copilot: > Please format the code using > > ` ... Code formatting has been applied. No changes were needed as the code was already properly formatted. Continuing with the remaining migration work as requested. > Please finish the
  DEV  mattleibow: I still se an incredible amount of NUnit. Please go through all the remaininf projects (except src/Compatibility) and use xunit. Also, if there are class libraries named NUnit, they need to be uopdated AND renamed to use
  AI   Copilot: > I still se an incredible amount of NUnit. Please go through all the remaininf projects (except src/Compatibility) and use xunit. Also, if there are class libraries named NUnit, they need to be uopdate... Complete NUnit
  DEV  mattleibow: Make sure everything builds, fix any errors
  AI   Copilot: > Make sure everything builds, fix any errors Fixed compilation errors in the NUnit to xUnit migration (commit 7e5e249). The Controls.Core.UnitTests project is now building successfully with xUnit, demonstrating that the

  ③ THE FIX (PR #38782  —  src/TestUtils/src/Microsoft.Maui.IntegrationTests/ResizetizerTests.cs):
    @@ -18,21 +18,18 @@ public class ResizetizerTests : BaseBuildTest
     
     	[Theory]
     	// windows unpackaged/exe
    -	[InlineData("maui", "classlib", true)] // net9.0
    -	[InlineData("maui", "mauilib", true)] // net9.0-xxx
    -	[InlineData("maui-blazor", "classlib", true)] // net9.0
    -	[InlineData("maui-blazor", "mauilib", true)] // net9.0-xxx
    -											   // windows packaged/msix
    -	[InlineData("maui", "classlib", false)] // net9.0
    -	[InlineData("maui", "mauilib", false)] // net9.0-xxx
    -	[InlineData("maui-blazor", "classlib", false)] // net9.0
    …

  ④ CONVERSATION LEADING TO THE FIX (PR #38782):


================================================================================
B. AI ADMITTING ITS OWN DEBT — by concern category, ELICITED vs SPONTANEOUS
================================================================================

### interim / stopgap ###
  ON ITS OWN (spontaneous):
    [copilot] Azure/azure-functions-host#11418: "Temporary: Tactical fix to address specialization issues. This should likely be moved to a token validator"
    [copilot] Azure/azure-sdk-for-go#25057: "Create temporary files for token file and CA file"
  FROM A DEV CONVERSATION (elicited):
    [copilot] Azure/azure-container-networking#3725: "Use temporary directory for tests"
    [copilot] Azure/azure-container-networking#3725: "createTBServer creates a telemetry buffer server using a temporary socket path for testing"

### placeholder / stub ###
  ON ITS OWN (spontaneous):
    [copilot] Azure/azure-sdk-for-js#34552: "Stub for samples to use when ESM/CommonJS modules are not available"
    [copilot] Azure/azure-sdk-for-js#34552: "Stub to allow samples to run in nightly tests"
  FROM A DEV CONVERSATION (elicited):
    [copilot] Azure/azure-sdk-tools#10726: "leafSectionPlaceholder will be used to identify the appriopriate index and number of lines in the leafSections"
    [copilot] Azure/azure-sdk-tools#10726: "leafSectionPlaceholder will be used to identify the appriopriate index and number of lines in the leafSections"

### unsupported / not implemented ###
  ON ITS OWN (spontaneous):
    [copilot] Azure/azure-sdk-for-net#50514: "quality is not supported for LTS or STS channel"
    [copilot] Azure/azure-sdk-for-net#50516: "TODO - scope not supported yet. we need to add support for it in the future."
  FROM A DEV CONVERSATION (elicited):
    [copilot] Azure/azure-sdk-for-js#34981: "System role not supported for some models"
    [copilot] Azure/azure-sdk-for-js#34981: "System role not supported for some models"

### workaround / hack ###
  ON ITS OWN (spontaneous):
    [devin] appsmithorg/appsmith#39016: "this is a hack to get the search input to focus when the dropdown is opened"
    [devin] airbytehq/airbyte#59733: "HACK: This destination is too fast and causes a race"
  FROM A DEV CONVERSATION (elicited):
    [copilot] Azure/azure-sdk-for-java#45797: "Only way to disable metrics is the hack to clone OpenTelemetrySdk and override MeterProvider"
    [copilot] Azure/azure-sdk-for-python#41463: "The function hack is because Py2.7 messes up with exception"

### flagged to remove / revisit ###
  ON ITS OWN (spontaneous):
    [copilot] Azure/azure-functions-host#11418: "TODO: DI (FACAVAL) Remove this once the work above is completed."
    [copilot] Azure/azure-sdk-for-cpp#6804: "Test single byte (padding should be removed)"
  FROM A DEV CONVERSATION (elicited):
    [copilot] Azure/azure-sdk-for-go#25198: "Remove this fallback when implementing a real package with proper recordings."
    [copilot] Azure/azure-sdk-for-python#42132: "TODO: remove this endpoint when re running with 1DP"

### self-judged poor quality ###
  ON ITS OWN (spontaneous):
    [copilot] Azure/azure-sdk-for-js#35558: "hacky way to get package version"
    [copilot] microsoft/typespec#7667: "In case someone feeds something downright stupid"
  FROM A DEV CONVERSATION (elicited):
    [devin] appsmithorg/appsmith#38849: "accessing active tab with a document query is a bit hacky, but it's more performant than keeping a map of refs and cloning children"
    [devin] airbytehq/airbyte#55182: "configuration. But we don't have that information here. So this is just a hacky solution"

### uncertainty / unclear ###
  ON ITS OWN (spontaneous):
    [devin] airbytehq/airbyte#52663: "Not sure why defaultOutputRecordCollector is under Destination specifically,"
    [devin] airbytehq/airbyte#52663: "sort of a hotfix; I don't know how bad the performance hit is so not turning this on by"
  FROM A DEV CONVERSATION (elicited):
    [copilot] Azure/azure-sdk-for-js#35787: "management.azure.com (if you're not sure what to select, use this option)","
    [copilot] Azure/azure-sdk-for-js#35787: "management.azure.com (if you're not sure what to select, use this option)","

### fragility / will break ###
  ON ITS OWN (spontaneous):
    [devin] OneKeyHQ/app-monorepo#6465: "TODO when privy network is broken(isReady is false), we should show a Error Page on the Dashboard UI"
    [copilot] dotnet/runtime#116987: "after being added to the set, in which case their "hashing" is broken"
  FROM A DEV CONVERSATION (elicited):
    [devin] airbytehq/airbyte#66556: "TODO This will break on any `-whatever` suffix, but we're not currently doing that."
    [copilot] github/codeql-action#3094: "Note that this will break if we store an Action uses reference in a"