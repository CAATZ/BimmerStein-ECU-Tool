# MS42/MS43 RAM agents

The application temporarily loads a RAM agent for faster reads and writes. It
leaves no persistent loader patch. MS42 uses 166,667 baud and MS43 uses 187,500
baud; the compatibility setting selects 125,000 baud for either family.

The host verifies the entry code and the agent checks its remaining upload before
execution. Transfers use CRC-protected packets. Boot writes are disabled by
default and require explicit user approval. Image finalization verifies the
firmware's checksums and reference identities before completing the write.

Supported firmware is selected by matching boot/service bytes. A software ID
alone is insufficient. A failed entry can use the stock diagnostic route only
when the connection has safely returned to that route. Interrupted writes retain
their recovery state instead of silently starting a second write.

The separate MS43 EEPROM agent reads and restores the serial EEPROM. EEPROM
restore is a separate user action; normal ROM writes do not clone that storage.

## Source and payload checks

`agent.asm` and `eeprom.asm` contain the source. Their manifests bind source,
assembly helper, symbols and embedded payloads. Verify the supplied artifacts
without an ECU:

```powershell
python -m engines.ms42_ms43.build --verify
python -m engines.ms42_ms43.build --verify --eeprom
```

The checks establish source/payload consistency. They do not establish electrical
or vehicle behavior. Keep the prior verified payload when changing an agent and
validate the replacement before hardware use.
