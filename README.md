<!-- Package overview and high-level API usage for the Autonomic Python SDK. -->

# Autonomic Python SDK

Python SDK for Autonomic Mirage Audio System/MRAD systems and direct Mirage
amplifier control such as standalone M-6250 devices.

This README covers package usage and the high-level client API. Wire-format
details live in [PROTOCOL.md](PROTOCOL.md).

## Client Layers

The SDK exposes two low-level clients and one unified high-level client.

Low-level clients:

- `MirageAudioSystem`: raw MRAD/MAS output, source, group, and zone protocol on
  TCP port `5006`.
- `MirageAmplifier`: raw direct amplifier protocol on TCP port `17037`.

High-level client:

- `AutonomicClient`: one object that auto-detects MRAD/MAS or direct amplifier
  mode and exposes object-first output/source helpers.

The SDK supports two main output-control paths.

`mrad` mode:

- MRAD/MAS zone and source control over TCP port `5006`.

`amplifier` mode:

- Direct amplifier control over TCP port `17037`.
- Used by standalone matrix amplifiers where `5006` is not open.
- The SDK exposes synthetic output/source browse lists and sends direct hex
  amplifier commands for source routing, volume, and mute.

`AutonomicClient` auto-detects the backend:

- If port `17037` is open, it uses direct amplifier mode.
- Otherwise, if port `5006` is open, it uses MRAD/MAS mode.

## Installation

```bash
python -m pip install -e .
```

The runtime dependency is Pydantic, used for typed output/source models.

## Protocol Reference

Wire formats, MRAD command names, direct amplifier opcodes, payload shapes, and
observed model/source mappings live in [PROTOCOL.md](PROTOCOL.md). Keep README
focused on package usage and use `PROTOCOL.md` when adding or validating
low-level protocol behavior.

## High-Level Python API

### Response Objects

`CommandResponse`:

- `command`: command string sent.
- `lines`: non-event response lines.
- `events`: parsed event lines.
- `payload`: parsed `BrowseResponse` for XML or legacy list responses.
- `first_line`: first response line or `None`.
- `text`: response lines joined by newline.

`BrowseResponse`:

- `kind`: root XML element name, such as `Zones`, `Sources`, or `ZoneGroups`.
- `attributes`: root XML attributes.
- `items`: list of `BrowseItem`.
- `raw`: raw response text.
- `total`, `start`, `more`: convenience accessors.
- Returned by low-level `browse_*` methods when raw protocol detail is needed.

`AutonomicOutput` and `AutonomicSource`:

- Pydantic models returned by ergonomic `list_outputs()` and `list_sources()`.
  Disabled items are omitted unless `include_disabled=True` is passed.
- `id`, `guid`, `name`, `attributes`, and `raw_xml` expose parsed protocol data.
- `disabled` is read-only state parsed from common disabled, enabled, hidden,
  and availability attributes when devices expose it.
- Objects are bound to the client that created them, so helper methods work:
  `client.list_outputs()[0].mute()`, `output.set_volume(50)`, and
  `source.assign_to(output)`.
- Source/output assignment is symmetric: `source.assign_to(output)` and
  `output.assign(source)` make the same client call.
- `AutonomicClient.all_outputs()` returns an `AutonomicOutputGroup`, an
  iterable fanout proxy with the same control helpers as a single output:
  `assign()`, `set_volume()`, `mute()`, `unmute()`, `volume_up()`,
  `volume_down()`, `set_power()`, and `set_is_on()`.
- `AutonomicClient.set_all_output_power()`, `all_on()`, and `all_off()` provide
  protocol-agnostic whole-system runtime power helpers. Direct amplifier mode
  uses the native all-output `01FF` power command. MRAD mode uses native
  `AllOff` when turning everything off and fans out `Power On <zone>` when
  turning everything on, because live probing showed `AllOn` and `PowerAll` are
  not MRAD commands.
- `AutonomicClient` supports a source alias map keyed by source GUID. Aliases
  change the returned object's display `name` and allow alias names in high
  level source assignment calls, while preserving the device's original source
  name in `source.attributes["name"]`. Aliases are local to the SDK and do not
  send any source rename or configuration command.
- `AutonomicClient.source_by_name(name)` and `output_by_name(name)` return
  typed objects by case-insensitive display name using the normal filtered list
  APIs. Pass `include_disabled=True` to search disabled items too.
- Output objects expose read-only state such as `is_on`, `muted`, `volume`,
  and current source fields when the device provides them.
- Output objects can set runtime power with `output.set_is_on()` or
  `output.set_power()`. On MRAD/MAS this updates the zone `PowerOn` state; in
  direct amplifier mode it sends the runtime `01` power command. It is not an
  enable/disable configuration-plane helper.
- Output/source objects do not expose enable or disable helpers. Source-name
  writes are intentionally limited to `rename_sources_to_low_level_input_labels()`.
- Client methods accept object instances, IDs, GUIDs, or names where applicable.

`AmplifierResponse`:

- `command`: direct amplifier command byte.
- `output`: decoded output number.
- `raw_output`: raw output byte.
- `data`: response payload bytes.
- `raw`: normalized raw response line.

`BrowseItem`:

- `kind`: item XML element name.
- `attributes`: item XML attributes.
- `children`: nested child element attributes, used by zone groups.
- `guid`, `id`, `name`: convenience accessors.
- `get_bool()` and `get_int()` convenience methods.

`StatusSnapshot`:

- `events`: parsed status events.
- `by_source`: nested dictionary, keyed by event source.
- `get(source, name, default=None)`: convenience accessor.

### `MirageAudioSystem`

Use for port `5006` MRAD/MAS control.

```python
from autonomic import MirageAudioSystem

with MirageAudioSystem("192.168.1.50") as mas:
    mas.initialize(host_hint="192.168.1.50")
    outputs = mas.list_outputs()
    sources = mas.list_sources()
    sources[0].assign_to(outputs[0])
    outputs[1].assign(sources[0])
    sources[0].assign_to_all_outputs()
    outputs[0].set_volume(60)
    outputs[0].unmute()
```

### `MirageAmplifier`

Use for direct port `17037` amplifier control.

```python
from autonomic import MirageAmplifier

amp = MirageAmplifier("192.168.1.60")
amp.get_device_id()
outputs = amp.list_outputs()  # includes direct amp status where reported
sources = amp.list_sources()
sources[6].assign_to(outputs[0])
outputs[1].assign(sources[6])
sources[0].assign_to_all_outputs()
outputs[0].set_power(True)
outputs[0].set_volume(40)
outputs[0].unmute()
```

### `AutonomicClient`

Use for seamless control when the device may be either a MAS/MRAD system or a
standalone direct amplifier. `AutonomicClient` auto-detects and initializes
itself during construction, preferring direct amplifier mode when both direct
amplifier and MRAD ports are available. If no host is provided, it connects to
`10.1.0.200`. Use `auto_initialize=False` only when building an offline object
for tests or when you need to patch/customize low-level clients before the first
protocol call.

```python
from autonomic import AutonomicClient

with AutonomicClient("192.168.1.50") as client:
    print(client.detect_mode())  # "mrad" or "amplifier"

    outputs = client.list_outputs()
    sources = client.list_sources()
    source = client.list_sources()[0]
    output = client.output_by_name("Kitchen")

    client.assign_source_to_output(source, output)
    output.set_is_on(True)
    output.set_volume(60)
    output.unmute()
```

Default direct-amplifier device layout and output names are loaded from
`autonomic/default_config.json`. Source aliases and remote eAudioCast source
definitions are empty by default, so local sources keep their model-specific
hardware labels unless you provide your own config:

```json
{
  "source_aliases": {},
  "direct_amplifier": {
    "devices": [
      {"device_id": "00D4", "host": "10.1.0.200", "model_byte": "0xB0"},
      {"device_id": "6012", "host": "10.1.0.201", "model_byte": "0xE9"}
    ],
    "output_names": {"1": "Kitchen", "9": "Grill"},
    "source_name_aliases": {},
    "local_sources_by_device_id": {},
    "remote_sources": [
      {
        "target_device_id": "6012",
        "source_device_id": "00D4",
        "source_id": 32,
        "name": ".200 OPT1",
        "guid": "source-device-guid"
      }
    ]
  }
}
```

`AutonomicClient` infers direct amplifier output/source counts and source-base
numbering from configured model bytes when available. Omitted `output_start`
values are assigned sequentially across the configured stack. For unconfigured
standalone direct amps, the direct amplifier client discovers device/model rows
once and caches the inferred layout for the lifetime of the client.
Remote eAudioCast entries are scoped to the device that owns the remote slot, so
the same native remote source id can exist on more than one amplifier without
colliding.

Pass `config_path="my-autonomic.json"` to use a different mapping file or
`source_aliases={...}` to override only source aliases:

```python
from autonomic import AutonomicClient

with AutonomicClient("192.168.1.50", source_aliases={"source-guid": "Alpha"}) as client:
    alpha = client.source_by_name("Alpha")
    client.all_outputs().assign(alpha)

with AutonomicClient("192.168.1.50", config_path="my-autonomic.json") as client:
    print(client.output_by_name("Kitchen").id)

with AutonomicClient("192.168.1.50") as raw_client:
    print(raw_client.list_sources()[0].attributes["name"])
```

In amplifier mode, source identifiers may be native direct source numbers or
device-qualified synthetic IDs such as `00D4:6`. In MRAD mode, source
identifiers may be source GUIDs, source IDs, or names.

Set every output to a source:

```python
from autonomic import AutonomicClient

with AutonomicClient("192.168.1.50") as client:
    source = client.list_sources()[0]
    client.all_outputs().assign(source)
```

Equivalent object-first form:

```python
with AutonomicClient("192.168.1.50") as client:
    outputs = client.all_outputs()
    outputs.assign(client.list_sources()[0])
    outputs.set_volume(50)
    outputs.unmute()
```

The same operation is available as a runnable example:

```bash
python examples/set_all_outputs_to_source.py 192.168.1.50
python examples/set_all_outputs_to_source.py 192.168.1.50 COAX2
python examples/set_all_outputs_to_source.py 192.168.1.60 1 --mode amplifier
```

Additional top-level `AutonomicClient` examples:

```bash
python examples/set_all_power_on_volume_to_50.py
python examples/unmute_set_all_to_50_and_power_on.py
python examples/add_eaudiocast_sources.py
python examples/set_all_zones_to_200_opt1.py
python examples/dump_system_status.py
python examples/short_dump.py
python examples/rename_sources_to_device_defaults.py
```

## Live Device Notes

The client has been sanity-checked against two device profiles:

MAS/MRAD system:

- `5006` open for MRAD.
- `17037` open for amplifier diagnostics.
- MRAD returned banner lines on connection.
- MRAD accepted targeted source assignment as
  `SetSource <source> <include_group_bool> <output>`.

Standalone M-6250-style amplifier:

- `5006` closed.
- `17037` open.
- `2FFF` returned an `AFFF...` device-id response.
- `AutonomicClient` detected amplifier mode and used direct amplifier commands.

## Tests

Run normal unit tests:

```bash
python -B -m unittest discover -s tests
```

Run live tests against a device:

```bash
AUTONOMIC_TEST_HOST=10.1.0.101 python -B -m unittest discover -s tests
AUTONOMIC_TEST_HOST=10.1.0.102 python -B -m unittest discover -s tests
```

Live tests are capability-based. MRAD tests are skipped on devices where port
`5006` is closed. Direct amplifier tests are skipped when port `17037` is
closed.
