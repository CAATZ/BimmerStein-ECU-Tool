# MS41 family RAM ownership investigation

> Isolated evidence report. It does not modify or replace current project documentation.

Collision-safe post-startup allocation map for the exact canonical MS41.0/MS41.1/MS41.2/MS41.3 images; not a universal firmware-family map.

## Family-safe intersection

| region | certified ranges | bytes | word-aligned bytes |
|---|---|---:|---:|
| XRAM | `0xD800-0xD83F`, `0xDB8F-0xDBA3`, `0xE847-0xE85F` | 110 | 108 |
| IRAM | `0xFDB6-0xFDDB` | 38 | 38 |

## Exact-image results

| variant | ECU/reference | SHA-256 | XRAM | IRAM |
|---|---|---|---|---|
| MS41.0 | `1429861` | `c61674c5812f5fe0a4a6f86e96311e9a8e540a905f9e7f681fdd045254249521` | `0xD800-0xD83F`, `0xDB8F-0xDC1F`, `0xE847-0xE85F` | `0xFD80-0xFDDB` |
| MS41.1 | `1437806` | `6129b8e8884cd8321c94546e2beba4c8b4afcfca0d7749a341a82cea37f99aa3` | `0xD800-0xD83F`, `0xDB8F-0xDBA3`, `0xE847-0xE85F` | `0xFC3F-0xFC41`, `0xFDB6-0xFDDB` |
| MS41.2 | `1406464` | `2d3ab7db6fe0f9a1f4680339f416aeeef43871730cd468cfd990f6dc80c03208` | `0xD800-0xD83F`, `0xDB8F-0xDC1F`, `0xE847-0xE85F` | `0xFC3F-0xFC41`, `0xFD80-0xFDDB` |
| MS41.3 | `12/41/42/59/60/85 family reference` | `89f31cbb70466ad39b23571e3c1c533251275182e8e2502c214a208e83f64487` | `0xD800-0xD83F`, `0xDB8F-0xDC1F`, `0xE847-0xE85F` | `0xFC3F-0xFC41`, `0xFD80-0xFDDB` |

All ranges are conditional post-startup allocations. Startup overwrites them, so none is reset-retained.

## Proof gates

| variant | reachable instructions | live unresolved sites | exact proof transfers | target-specific sites | unclosed |
|---|---:|---:|---:|---:|---:|
| MS41.0 | 7807 | 387 | 222 | 165 | 0 |
| MS41.1 | 9502 | 652 | 292 | 360 | 0 |
| MS41.2 | 16436 | 658 | 654 | 4 | 0 |
| MS41.3 | see source report | see source report | see source report | see source report | 0 |

## MS41.0 complete allocation-class map

### XRAM

| range | bytes | class |
|---|---:|---|
| `0xD800-0xD83F` | 64 | conditionally available after startup |
| `0xD840-0xDB8E` | 847 | owned/reserved; no allocation certificate |
| `0xDB8F-0xDC1F` | 145 | conditionally available after startup |
| `0xDC20-0xE846` | 3111 | owned/reserved; no allocation certificate |
| `0xE847-0xE85F` | 25 | conditionally available after startup |
| `0xE860-0xF7FF` | 4000 | owned/reserved; no allocation certificate |
### IRAM

| range | bytes | class |
|---|---:|---|
| `0xFA00-0xFD7F` | 896 | owned/reserved; no allocation certificate |
| `0xFD80-0xFDDB` | 92 | conditionally available after startup |
| `0xFDDC-0xFDFF` | 36 | owned/reserved; no allocation certificate |

### Target-specific indirect proof families

| proof family | sites | effective address envelopes |
|---|---:|---|
| FC50 bounded lookup | 1 | `0x0000-0x01FE`, `0x2B4A-0x2D48` |
| byte-indexed E885 writer | 1 | `0xE885-0xE984` |
| calibration parser and lookup | 11 | `0x0000-0xC002`, `0xFB5A-0xFB5A` |
| high serial helpers | 2 | `0xE44B-0xE4DA`, `0xED01-0xF0FF` |
| high serial interrupt block | 1 | `0xA000-0xBFFF`, `0xFA62-0xFA73`, `0xFB2C-0xFB43` |
| r0 software-stack frame | 70 | `0xFA00-0xFA45` |
| record and checksum helpers | 70 | `0xA000-0xCFFF`, `0xE900-0xF7FF`, `0xFB00-0xFBFF` |
| status pointer helpers | 5 | `0xD000-0xD0FF`, `0xED00-0xF1FF` |
| timer and serial interrupt tables | 4 | `0xA000-0xBFFF`, `0xF100-0xF1FF`, `0xFB2C-0xFB43` |

## MS41.1 complete allocation-class map

### XRAM

| range | bytes | class |
|---|---:|---|
| `0xD800-0xD83F` | 64 | conditionally available after startup |
| `0xD840-0xDB8E` | 847 | owned/reserved; no allocation certificate |
| `0xDB8F-0xDBA3` | 21 | conditionally available after startup |
| `0xDBA4-0xE846` | 3235 | owned/reserved; no allocation certificate |
| `0xE847-0xE85F` | 25 | conditionally available after startup |
| `0xE860-0xF7FF` | 4000 | owned/reserved; no allocation certificate |
### IRAM

| range | bytes | class |
|---|---:|---|
| `0xFA00-0xFC3E` | 575 | owned/reserved; no allocation certificate |
| `0xFC3F-0xFC41` | 3 | conditionally available after startup |
| `0xFC42-0xFDB5` | 372 | owned/reserved; no allocation certificate |
| `0xFDB6-0xFDDB` | 38 | conditionally available after startup |
| `0xFDDC-0xFDFF` | 36 | owned/reserved; no allocation certificate |

### Target-specific indirect proof families

| proof family | sites | effective address envelopes |
|---|---:|---|
| FC50 bounded lookup | 1 | `0x0000-0x01FE`, `0x2DB6-0x2FB4` |
| byte-indexed E885 writer | 1 | `0xE885-0xE984` |
| calibration parser and lookup | 23 | `0x0000-0xC002`, `0xFB24-0xFB24` |
| descriptor and record bridges | 20 | `0xA000-0xBFFF`, `0xE862-0xE864`, `0xEA00-0xEEFF`, `0xF692-0xF6D1`, `0xFC3C-0xFC3C`, `0xFC82-0xFC99` |
| diagnostic pointer profiles | 4 | `0xA000-0xBFFF`, `0xE421-0xE51F`, `0xE520-0xE620` |
| flash-protocol pointer | 2 | `0x0000-0x0000`, `0x2200-0x2200`, `0xE320-0xE528` |
| high serial helpers | 2 | `0xF2DA-0xF4A0` |
| high serial interrupt block | 3 | `0xA000-0xBFFF`, `0xF692-0xF6EF`, `0xFA62-0xFA73`, `0xFC82-0xFC99` |
| immutable dispatch table | 1 | `0xA400-0xA40F` |
| immutable metadata lookup | 1 | `0xA000-0xAD20` |
| indexed record tables | 76 | `0xA000-0xCFFF`, `0xEEA0-0xF7FF` |
| paired native objects | 43 | `0xF030-0xF1A7` |
| r0 software-stack frame | 121 | `0xFA00-0xFA45` |
| record updater family | 51 | `0xA000-0xBFFF`, `0xE862-0xE864`, `0xE8D0-0xF7FF`, `0xFC3C-0xFC3C`, `0xFC82-0xFC9D` |
| six-result lookup | 1 | `0xF59E-0xF5BC` |
| status pointer helpers | 8 | `0x0000-0x0000`, `0xD000-0xD00A`, `0xF700-0xF7FF` |
| timer destination table | 2 | `0xA000-0xBFFF`, `0xFC82-0xFC99` |

## MS41.2 complete allocation-class map

### XRAM

| range | bytes | class |
|---|---:|---|
| `0xD800-0xD83F` | 64 | conditionally available after startup |
| `0xD840-0xDB8E` | 847 | owned/reserved; no allocation certificate |
| `0xDB8F-0xDC1F` | 145 | conditionally available after startup |
| `0xDC20-0xE846` | 3111 | owned/reserved; no allocation certificate |
| `0xE847-0xE85F` | 25 | conditionally available after startup |
| `0xE860-0xF7FF` | 4000 | owned/reserved; no allocation certificate |
### IRAM

| range | bytes | class |
|---|---:|---|
| `0xFA00-0xFC3E` | 575 | owned/reserved; no allocation certificate |
| `0xFC3F-0xFC41` | 3 | conditionally available after startup |
| `0xFC42-0xFD7F` | 318 | owned/reserved; no allocation certificate |
| `0xFD80-0xFDDB` | 92 | conditionally available after startup |
| `0xFDDC-0xFDFF` | 36 | owned/reserved; no allocation certificate |

### Target-specific indirect proof families

| proof family | sites | effective address envelopes |
|---|---:|---|
| FC50 bounded lookup | 1 | `0x0000-0x01FE`, `0x2AD6-0x2CD4` |
| fixed optional result byte | 1 | `0xE8EB-0xE8EB` |
| immutable dispatch table | 1 | `0xA400-0xA40F` |
| r0 software-stack frame | 1 | `0xFA00-0xFA45` |

## MS41.3 complete allocation-class map

### XRAM

| range | bytes | class |
|---|---:|---|
| `0xD800-0xD83F` | 64 | conditionally available after startup |
| `0xD840-0xDB8E` | 847 | owned/reserved; no allocation certificate |
| `0xDB8F-0xDC1F` | 145 | conditionally available after startup |
| `0xDC20-0xE846` | 3111 | owned/reserved; no allocation certificate |
| `0xE847-0xE85F` | 25 | conditionally available after startup |
| `0xE860-0xF7FF` | 4000 | owned/reserved; no allocation certificate |
### IRAM

| range | bytes | class |
|---|---:|---|
| `0xFA00-0xFC3E` | 575 | owned/reserved; no allocation certificate |
| `0xFC3F-0xFC41` | 3 | conditionally available after startup |
| `0xFC42-0xFD7F` | 318 | owned/reserved; no allocation certificate |
| `0xFD80-0xFDDB` | 92 | conditionally available after startup |
| `0xFDDC-0xFDFF` | 36 | owned/reserved; no allocation certificate |

## Loader boundaries

- All four stock images use `0xDC20-0xE31F` as the authenticated DS2 download target, `0xE320-0xE41F` for the RAM flash driver, and `0xE420-0xE528` for flash-protocol state.
- Current Soft-BSL support is limited to MS41.2/MS41.3. Its chunk buffer is `0xE000-0xE3FF` and CRC table is `0xFD60-0xFD7F`; neither overlaps the certified ranges.
- MS41.0/MS41.1 RAM non-collision does not imply Soft-BSL boot, entry, transport, or flash support.

## Limits

- No unlisted range is certified available.
- A different firmware hash needs a new run.
- Soft-BSL transport/entry support is separate from RAM non-collision.
- Hardware execution and readback remain required before deployment.
