# External Driver Web Research Delta

Research date: 2026-05-28

This note records public-source research and downloaded-driver comparisons that
are not already covered by `DRIVER_REVERSE_ENGINEERING.md` or `PROTOCOL.md`.
It intentionally avoids repeating the existing protocol extraction except where
a newly discovered package changes or challenges the earlier picture.

## Sources Checked

- Autonomic/SnapAV product downloads:
  - https://autonomic.biz/ma6/
  - https://www.snapav.com/shop/en/snapav/au-m6-a
- Control4 public driver catalog:
  - https://drivers.control4.com/solr/drivers/browse?fq=manufacturer%3A%22Autonomic+Controls%22&q=
  - https://drivers.control4.com/solr/drivers/browse?fq=manufacturer%3A%22Autonomic+Controls%2C+Inc%22&q=
- Crestron Home third-party driver:
  - https://cpllc.net/product/autonomic-eseries/
  - https://autonomic.atlassian.net/wiki/spaces/ASKB/pages/1632370689
- Exact-name and filename searches for the local RTI, URC, AMX, Crestron,
  Control4, and Savant artifacts.

## Current Public Bundles Match `external/`

The Autonomic/SnapAV module downloads linked from the MA6 product page were
downloaded again and hashed. Each live archive matched the corresponding file
already present under `external/`, so these were not copied into the repository.

| Live public bundle | Local file | SHA-256 |
| --- | --- | --- |
| `Autonomic AMX Module.zip` | `external/Autonomic AMX Module.zip` | `abd63bf21bc99ff0f827cb804266aecc0ece52c6101ea4822fba87df46df0520` |
| `Autonomic Control4 Modules.zip` | `external/Autonomic Control4 Modules.zip` | `9ddc8ca2b9a65331c1178b77ec82bc2429ca7cdc86c082477875b56da15c8b21` |
| `Autonomic Crestron Modules.zip` | `external/Autonomic Crestron Modules.zip` | `fe1246372264da964a1e42d483819a846250823a596cd0a30e88f9385c0b9109` |
| `Autonomic URC Driver.zip` | `external/Autonomic URC Driver.zip` | `58e9bb19ddc3a12c05d90da18e6c27479398d6d167c1e7a40a6a2f6c53929566` |
| `Autonomic_MMS_RTI.zip` | `external/Autonomic_MMS_RTI.zip` | `9580a31907baccc1f05fcdc78662137b5a37e3f20a906154636bb062ebfc60f5` |

Result: no newer public AMX, RTI, URC, Crestron SIMPL/SIMPL+, or top-level
Control4 bundle was found from the vendor download pages.

## Control4 Amp Catalog Drivers

The Control4 catalog page for manufacturer `Autonomic Controls` currently lists
five certified amp drivers at version `265` and several older non-certified
2019 entries.

Downloaded certified v265 drivers:

- `AutonomicM120e32DistributedZones.c4z`
- `AutonomicM40032CentralizedZones.c4z`
- `AutonomicM401e32DistributedZones.c4z`
- `AutonomicM80032CentralizedZones.c4z`
- `AutonomicM801e32DistributedZones.c4z`

Comparison against the nested `.c4z` files inside
`external/Autonomic Control4 Modules.zip` showed identical driver payloads
except for an added `www/c4driversupport.xml` certification file. That file
records Control4 certification on `12/19/2024` for Control4 release `3.4.3`.

The older catalog entries include an `Autonomic MSB20e` driver at version `256`,
which is a model not present in the current local bundle. However, the public
download URLs returned HTTP 404 on 2026-05-28, so the MSB20e package could not
be downloaded or analyzed.

## Newer Control4 MMS/MCP Drivers: v699

The Control4 catalog page for manufacturer `Autonomic Controls, Inc` has newer
MMS, MCP, instance, and music-service drivers at version `699`. The local
Control4 bundle contains the prior `698` family.

Persisted location:

```text
external/control4_mms_v699/
```

Observed catalog/package metadata:

- Version: `699`
- Service and instance modified timestamp: `2026-03-13 17:15`
- MCP modified timestamp: `2026-02-25 13:55`
- Control4 certification files target Control4 software release `4.1.0`
- MMS/MCP transport remains TCP port `5006`
- Instance and service drivers expose a `media_service` proxy on binding `5001`

Downloaded driver set:

- `AutonomicMMSAmazonMusic`
- `AutonomicMMSAppleMusic`
- `AutonomicMMSAudacy`
- `AutonomicMMSCalmRadio`
- `AutonomicMMSDeezer`
- `AutonomicMMSFavorites`
- `AutonomicMMSInstance`
- `AutonomicMMSLiveOne`
- `AutonomicMMSMCP1e`
- `AutonomicMMSMCP2`
- `AutonomicMMSMCP3e`
- `AutonomicMMSMCP5`
- `AutonomicMMSMCP5e`
- `AutonomicMMSMCPM3`
- `AutonomicMMSMCPM5Pro`
- `AutonomicMMSMurfie`
- `AutonomicMMSMusic`
- `AutonomicMMSNapster`
- `AutonomicMMSPandora`
- `AutonomicMMSQobuz`
- `AutonomicMMSSiriusXM`
- `AutonomicMMSSoundMachine`
- `AutonomicMMSSpotify`
- `AutonomicMMSTIDAL`
- `AutonomicMMSTuneIn`
- `AutonomicMMSeSeriesInstance`
- `AutonomicMMSiHeartRadio`

Visible deltas from local v698:

- Every `driver.lua.encrypted` differs, so there are real internal changes.
- The Lua remains encrypted, so the MMS MCP / AutonomicNet wire protocol is
  still not recoverable from these packages.
- Readable helper libraries such as `ac_timer.lua`, `ac_logging.lua`,
  `ac_memory.lua`, `ac_utils.lua`, and `ac_actions.lua` are unchanged in the
  MCP drivers.
- `driver.xml` changes are mostly metadata: version, copyright year, modified
  timestamp, certification support XML, and the service proxy binding.
- Instance-driver manifests also include small icon-reference changes for
  clear-queue UI actions.

Result: v699 is newer and should be retained as a distinct downloaded family
if this repository wants a corpus of driver versions. It does not expose new
plaintext protocol commands beyond what the current docs already describe.

### v699 Download Hashes

| Driver | SHA-256 |
| --- | --- |
| `AutonomicMMSAmazonMusic.c4z` | `b9956d438a1527afefb48ed0db725c2c29315ce3905149b9b6b4831ef829982a` |
| `AutonomicMMSAppleMusic.c4z` | `eb1e27ed3fc61496607a1d2b927671e924944215769097d31389cf3b18230978` |
| `AutonomicMMSAudacy.c4z` | `c0b1e94b0a8c3e1e4e4489f4b29397e1c6541de90e749739c6808f615df3b370` |
| `AutonomicMMSCalmRadio.c4z` | `9d2af038fb1fa41dd74b0461aeff6fa0907b1b1a8a146910dcca8ac8dcbff427` |
| `AutonomicMMSDeezer.c4z` | `35ef1597b94a030d58f1aa18a31f9b44ebfff988ce464a68cb3f9710ccb84aa5` |
| `AutonomicMMSFavorites.c4z` | `ea9ec90cc58bbef3df162e056668ea074c4f9210523ef9119bdeb94e7cbc7b4c` |
| `AutonomicMMSInstance.c4z` | `491b60c14e4b74e6a9a6b952b283b9e3f4dcaee8cdec71948ac9df900dc7df94` |
| `AutonomicMMSLiveOne.c4z` | `da2a0caf16674c6ee61396a99303e3f2a60aaa8d495c1728ca549194dc3a2d6e` |
| `AutonomicMMSMCP1e.c4z` | `6f2287430f9b3ced4ce2dad97a28b2c90b50daedfe36ba5443dbb84c4f38ef43` |
| `AutonomicMMSMCP2.c4z` | `e4ada6b75f4b713eef7813db2cd7870f5321a506b379fefb4c66a3855a7eda28` |
| `AutonomicMMSMCP3e.c4z` | `bdbc0e2501008524e4bbb7ff0b5c29707341f442c3c6addb1e5e6fb9bd7b6dc0` |
| `AutonomicMMSMCP5.c4z` | `51e235099408799a9034978a15393539e0b46b4421dbc519538843554fb973ce` |
| `AutonomicMMSMCP5e.c4z` | `60f72bc86d4f6e1d9d44bfbd2b094497fdea45bd7ca7f57c11a694da9ec6f18d` |
| `AutonomicMMSMCPM3.c4z` | `c3cd81c41b212c955e9fd1da19491e7cc8c07e2ae9c49c4cd5f52176e694f181` |
| `AutonomicMMSMCPM5Pro.c4z` | `cf26373d03e8a69739cb467ffdf0c005788efcde9a63d813a4b227c22655ada9` |
| `AutonomicMMSMurfie.c4z` | `7dc3834e93b6dd235dc5476aadff3076f8dfe3378a8f890eacdac9c7b17d0744` |
| `AutonomicMMSMusic.c4z` | `de5c3d9fa983a441ac1b7a92a79772dd6e8ae290369fc8ace56f973a2b644f22` |
| `AutonomicMMSNapster.c4z` | `1661e30a412fa1e5d4428cff178f9ea5b827c85634c39519c35e725a6251c42f` |
| `AutonomicMMSPandora.c4z` | `9cc5c489154294ebaf7837ff30ee84c009a845a47119834aac1d91e738fb5572` |
| `AutonomicMMSQobuz.c4z` | `fc86435bf32d7b150c3791a0c3ce53148fb5b6cc8b568d8c9dfcc153d25e3d8a` |
| `AutonomicMMSSiriusXM.c4z` | `ebae1a1d3f274b4cd0565953817c0e0fc5a1df046ec836cb47bf1a7b039d1fc6` |
| `AutonomicMMSSoundMachine.c4z` | `0d921309ca73df615ff012571601d7c8f26967364e035dd3ec09a781b581df52` |
| `AutonomicMMSSpotify.c4z` | `024b5c7bc8069d42961200895fec613276037609b458968035a8d20c5b4d5ff2` |
| `AutonomicMMSTIDAL.c4z` | `00a7438e5ce811f74cc16590405bf7a3eee57900f0888ed68fd2c7d54025a454` |
| `AutonomicMMSTuneIn.c4z` | `eddbad2c003d21281057df32442309993cc27467afa03df426bb867055f11f9a` |
| `AutonomicMMSeSeriesInstance.c4z` | `a0cd1235687bbf6e394fdade5722048b9173d49ad594582e701f90ab0c474e80` |
| `AutonomicMMSiHeartRadio.c4z` | `8b826153b43a7cffa4896dd53fc79442f2d1800e503a984c8e9dc56c4f178c8f` |

## Distinct Crestron Home Driver

Control Programming LLC publishes a distinct Crestron Home driver package for
Autonomic eSeries amplifiers. This is not the same family as the existing
Crestron SIMPL/SIMPL+ modules under `external/Autonomic Crestron Modules.zip`.

Persisted files:

```text
external/crestron_home_cpllc_eseries/CPLLC.Autonomic.eSERIES.pkg
external/crestron_home_cpllc_eseries/CPLLC.Autonomic.eSERIES.pdf
```

Hashes:

| File | SHA-256 |
| --- | --- |
| `CPLLC.Autonomic.eSERIES.pkg` | `6b9e3467de410cc77dd5da2f1f846819f8eeceaca56d5dbf3d00b1835e48efb1` |
| `CPLLC.Autonomic.eSERIES.pdf` | `14cf8f6d3d4feb4bf9e248a47145a63eb302c592fdf27359b6f3ac1b1be694c8` |

Package contents:

- `CPLLC.Autonomic.eSERIES.dat`
- `CPLLC.Autonomic.eSERIES.dll`
- `CPLLC.Autonomic.eSERIES.pdf`
- package copyright/version marker files

Manifest and string analysis:

- Driver version: `1.00.003.0001`
- SDK version: `4.0.0`
- Supported platform: `4-Series`
- Device type: `AV Switcher`
- Manufacturer: `Autonomic`
- Default transport: insecure TCP port `17037`
- Class: `CPLLC.Drivers.AVSwitcher`
- The `.dat` manifest models 32 audio inputs and 32 audio outputs.
- The `.dll` exposes RAD/AV-switcher route formatting like
  `Z{output}INP{input};` and strings for route, power, mute, volume, bass,
  and treble controls.
- Plain strings identify opcode families `01`, `02`, `03`, `04`, `05`, and
  `06`, aligning with the core MRAD operations already documented.
- No plaintext evidence was found for the broader Control4 amp-only operations
  such as loudness, max volume, delay, input gain, remote-source definitions,
  keypad events, or network identity repair.

One caution: embedded feedback identifiers in the CPLLC driver include zone
headers through `...20` for output 32, while the readable RTI, Control4, and
Crestron SIMPL drivers encode logical zone 32 as `80`. This may be a RAD-layer
identifier, a driver limitation, a driver bug, or an alternate firmware-accepted
zone mapping. Treat it as a candidate discrepancy until validated against live
hardware or by deeper .NET decompilation.

## Negative Findings

- No newer public RTI `.rtidriver`, URC `.tcm`/`.tcm2`, AMX `.AXW`, Savant
  profile, or Crestron SIMPL/SIMPL+ driver was found beyond the archives
  already present in `external/`.
- Exact filename searches for the current local archives generally led back to
  the same Autonomic/SnapAV download paths.
- The only clearly newer downloadable family found was Control4 MMS/MCP v699.
- The only clearly distinct downloadable family found was the Crestron Home
  CPLLC eSeries package.
