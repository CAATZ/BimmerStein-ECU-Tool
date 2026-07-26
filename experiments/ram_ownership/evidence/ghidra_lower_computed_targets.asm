0000dc: c60806fb       scxt 0xfe10,#0xfb06
0000e0: cc00           nop
0000e2: 0901           addb RL0,#0x1
0000e4: c006           movbz r6,RL0
0000e6: 0066           add r6,r6
0000e8: d4861eae       mov r8,[r6+#0xae1e]
0000ee: 490a           cmpb RL0,[r2]
0000f0: 3d23           jmpr cc_NE,0x000138            -> 000138
0000f2: c2f680fa       movbz r6,0xfa80
0000f6: f4e20100       movb RL7,[r2+#0x1]
0000fa: 3d0f           jmpr cc_NE,0x00011a            -> 00011a
0000fc: c68800f8       scxt 0xff10,#0xf800
000100: f7fc5ffa       movb 0xfa5f,RL6
000104: d4620200       mov r6,[r2+#0x2]
000108: 02f652fe       add r6,0xfe52
00010c: f6f68cfe       mov 0xfe8c,r6
000110: 1aaa0c0f       bfldh 0xff54,#0xf,#0xc
000114: fc88           pop 0xff10
000116: 0824           add r2,#0x4
000118: 0d0f           jmpr cc_UC,0x000138            -> 000138
00011a: c68800f8       scxt 0xff10,#0xf800
00011e: f4c626af       movb RL6,[r6+#0xaf26]
000122: f7fc5ffa       movb 0xfa5f,RL6
000126: d4620200       mov r6,[r2+#0x2]
00012a: 02f652fe       add r6,0xfe52
00012e: f6f68cfe       mov 0xfe8c,r6
000132: 1aaa0c0f       bfldh 0xff54,#0xf,#0xc
000136: fc88           pop 0xff10
000138: 490b           cmpb RL0,[r3]
00013a: 3d14           jmpr cc_NE,0x000164            -> 000164
00013c: 7ee0           bclr 0xffc0.0x7
00013e: c2f680fa       movbz r6,0xfa80
000142: 9a2f0380       jnb 0xfd5e.0x8,0x00014c        -> 00014c
000146: f4e6caad       movb RL7,[r6+#0xadca]
00014a: 0d02           jmpr cc_UC,0x000150            -> 000150
00014c: f4e6b8ad       movb RL7,[r6+#0xadb8]
000150: f7fe5efa       movb 0xfa5e,RL7
000154: d4630200       mov r6,[r3+#0x2]
000158: 02f652fe       add r6,0xfe52
00015c: f6f68efe       mov 0xfe8e,r6
000160: 1aaac0f0       bfldh 0xff54,#0xf0,#0xc0
000164: 9a2f6380       jnb 0xfd5e.0x8,0x00022e        -> 00022e
000168: aa000280       jbc 0xfd00.0x8,0x000170        -> 000170
00016c: ea00fc42       jmpa cc_UC,0x0042fc            -> 0042fc
00038e: f6237afa       mov 0xfa7a,0xfe46
000392: 1aa3e0f0       bfldh 0xff46,#0xf0,#0xe0
000396: ea00ee40       jmpa cc_UC,0x0040ee            -> 0040ee
00039a: 1aa300f0       bfldh 0xff46,#0xf0,#0x0
00039e: 8f00           bset 0xfd00.0x8
0003a0: f2f636fc       mov r6,0xfc36
0003a4: 2d08           jmpr cc_EQ,0x0003b6            -> 0003b6
0003a6: 02f652fe       add r6,0xfe52
0003aa: f6f696fe       mov 0xfe96,r6
0003ae: 1aabd0f0       bfldh 0xff56,#0xf0,#0xd0
0003b2: 7ec7           bclr 0xff8e.0x7
0003b4: bee0           bclr 0xffc0.0xb
0003b6: e6f246fa       mov r2,#0xfa46
0003ba: e6f34efa       mov r3,#0xfa4e
0003be: 0f00           bset 0xfd00.0x0
0003c0: e10c           movb RL6,#0x0
0003c2: f7fc62da       movb 0xda62,RL6
0003c6: e11c           movb RL6,#0x1
0003c8: f7fc80fa       movb 0xfa80,RL6
0003cc: f7fcddf6       movb 0xf6dd,RL6
0003d0: 6f01           bset 0xfd02.0x6
0003d2: 9f2f           bset 0xfd5e.0x9
0003d4: f2f6def6       mov r6,0xf6de
0003d8: f6f6e0f6       mov 0xf6e0,r6
0003dc: e1cc           movb RL6,#0xc
0003de: f7fcdff6       movb 0xf6df,RL6
0003e2: ea004e4e       jmpa cc_UC,0x004e4e            -> 004e4e
000412: ea00ee40       jmpa cc_UC,0x0040ee            -> 0040ee
000440: ea00ee40       jmpa cc_UC,0x0040ee            -> 0040ee
000444: ea00ee40       jmpa cc_UC,0x0040ee            -> 0040ee
0004fe: f2f59efe       mov r5,0xfe9e
000502: 22f578fa       sub r5,0xfa78
00030c: 0aa90f00       bfldl 0xff52,#0x0,#0xf
000310: 0fe0           bset 0xffc0.0x0
000312: 0d13           jmpr cc_UC,0x00033a            -> 00033a
000314: 0aa9f000       bfldl 0xff52,#0x0,#0xf0
000318: 1fe0           bset 0xffc0.0x1
00031a: 0d0f           jmpr cc_UC,0x00033a            -> 00033a
00031c: 1aa9000f       bfldh 0xff52,#0xf,#0x0
000320: 2fe0           bset 0xffc0.0x2
000322: 0d0b           jmpr cc_UC,0x00033a            -> 00033a
000324: 1aa900f0       bfldh 0xff52,#0xf0,#0x0
000328: 3fe0           bset 0xffc0.0x3
00032a: 0d07           jmpr cc_UC,0x00033a            -> 00033a
00032c: 0aaa0f00       bfldl 0xff54,#0x0,#0xf
000330: 4fe0           bset 0xffc0.0x4
000332: 0d03           jmpr cc_UC,0x00033a            -> 00033a
000334: 0aaaf000       bfldl 0xff54,#0x0,#0xf0
000338: 5fe0           bset 0xffc0.0x5
001106: 0aaaf000       bfldl 0xff54,#0x0,#0xf0
001142: 1aa9000f       bfldh 0xff52,#0xf,#0x0
001146: 2fe0           bset 0xffc0.0x2
001148: 0d0b           jmpr cc_UC,0x001160            -> 001160
0011e6: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0011ea: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0011ee: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0011f2: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0011f6: bff0           bset r0.0xb
0011f8: 7fb2           bset 0xff64.0x7
0011fa: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
00121a: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
00121e: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
001222: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
001226: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
00122a: e6f252fa       mov r2,#0xfa52
00122e: e6f35afa       mov r3,#0xfa5a
001232: 0e00           bclr 0xfd00.0x0
001234: e11c           movb RL6,#0x1
001236: f7fc62da       movb 0xda62,RL6
00123a: e12c           movb RL6,#0x2
00123c: f7fc80fa       movb 0xfa80,RL6
001240: f7fcddf6       movb 0xf6dd,RL6
001244: 3f01           bset 0xfd02.0x3
001246: 9f2f           bset 0xfd5e.0x9
001248: f2f6def6       mov r6,0xf6de
00124c: f6f6e0f6       mov 0xf6e0,r6
001250: e1cc           movb RL6,#0xc
001252: f7fcdff6       movb 0xf6df,RL6
001256: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
00125a: f3fc69fc       movb RL6,0xfc69
001274: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0013ae: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0013b2: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0013b6: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0013c2: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0013c6: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0013ca: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
0013ce: 8ff0           bset r0.0x8
001402: e6f246fa       mov r2,#0xfa46
001406: e6f34efa       mov r3,#0xfa4e
00140a: 0f00           bset 0xfd00.0x0
00140c: e14c           movb RL6,#0x4
00140e: f7fc62da       movb 0xda62,RL6
001412: e15c           movb RL6,#0x5
001414: f7fc80fa       movb 0xfa80,RL6
001418: f7fcddf6       movb 0xf6dd,RL6
00141c: 3f01           bset 0xfd02.0x3
00141e: 9f2f           bset 0xfd5e.0x9
001420: f2f6def6       mov r6,0xf6de
001424: f6f6e0f6       mov 0xf6e0,r6
001428: e1cc           movb RL6,#0xc
00142a: f7fcdff6       movb 0xf6df,RL6
00142e: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
001432: f3fc69fc       movb RL6,0xfc69
001436: f7fcdcf6       movb 0xf6dc,RL6
00143a: 1f6e           bset 0xfddc.0x1
001450: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
001454: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
001458: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
00145c: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
001460: ea00f44f       jmpa cc_UC,0x004ff4            -> 004ff4
001464: 9ff0           bset r0.0x9
00152c: ea007251       jmpa cc_UC,0x005172            -> 005172
0016a0: 43f2bcfa       cmpb RL1,0xfabc
00170e: 43f2bcfa       cmpb RL1,0xfabc
001714: e7f11000       movb RH0,#0x10
0017ee: 43f2bcfa       cmpb RL1,0xfabc
007178: e118           movb RL4,#0x1
00718c: da037e1b       calls 0x031b7e                 -> 031b7e
0071b2: f2f43ee9       mov r4,0xe93e
0071d4: 2e13           bclr 0xfd26.0x2
0071d8: 6e13           bclr 0xfd26.0x6
0071de: 4e23           bclr 0xfd46.0x4
0071e2: 0f14           bset 0xfd28.0x0
0071e6: 8e29           bclr 0xfd52.0x8
0071ec: 9a2b09e0       jnb 0xfd56.0xe,0x007202        -> 007202
00721c: e7f82e00       movb RL4,#0x2e
00724e: e7f83100       movb RL4,#0x31
007280: f68e88f7       mov 0xf788,0xff1c
007292: f68e88f7       mov 0xf788,0xff1c
0072ae: f2f410ea       mov r4,0xea10
0072d8: e7f82000       movb RL4,#0x20
0072f0: 9a140f90       jnb 0xfd28.0x9,0x007312        -> 007312
00731e: 4e06           bclr 0xfd0c.0x4
007322: 5e06           bclr 0xfd0c.0x5
007326: e03c           mov r12,#0x3
007334: e188           movb RL4,#0x8
0073a8: 6e03           bclr 0xfd06.0x6
0073b2: 6e20           bclr 0xfd40.0x6
0073b6: e014           mov r4,#0x1
0073ec: f3f89af1       movb RL4,0xf19a
007432: f3f89af1       movb RL4,0xf19a
0074b2: 9f21           bset 0xfd42.0x9
0074b6: af21           bset 0xfd42.0xa
0074ba: bf21           bset 0xfd42.0xb
0074be: 9a1d0d30       jnb 0xfd3a.0x3,0x0074dc        -> 0074dc
0074de: 9a1d0970       jnb 0xfd3a.0x7,0x0074f4        -> 0074f4
00750e: e6fc1e00       mov r12,#0x1e
00751e: 9f03           bset 0xfd06.0x9
007522: 9a2a2e00       jnb 0xfd54.0x0,0x007582        -> 007582
00758e: 9a2a1110       jnb 0xfd54.0x1,0x0075b4        -> 0075b4
007604: 4f2a           bset 0xfd54.0x4
007608: f68eb0f7       mov 0xf7b0,0xff1c
007612: 9a2a0d20       jnb 0xfd54.0x2,0x007630        -> 007630
007636: 3f1f           bset 0xfd3e.0x3
00763a: 6f17           bset 0xfd2e.0x6
00763e: f3f81bf5       movb RL4,0xf51b
007662: e7f83c00       movb RL4,#0x3c
007694: cf04           bset 0xfd08.0xc
007698: e6fc1b00       mov r12,#0x1b
0076a8: e148           movb RL4,#0x4
0076da: 7e04           bclr 0xfd08.0x7
0076de: e7f8cc00       movb RL4,#0xcc
0076e8: e118           movb RL4,#0x1
00776a: 9e23           bclr 0xfd46.0x9
00776e: e014           mov r4,#0x1
00777e: e02c           mov r12,#0x2
00778e: 0f23           bset 0xfd46.0x0
007792: e6f40008       mov r4,#0x800
0077b0: 1f23           bset 0xfd46.0x1
0077ca: af20           bset 0xfd40.0xa
0077d0: 6f09           bset 0xfd12.0x6
0077e0: e118           movb RL4,#0x1
0077ee: 5e22           bclr 0xfd44.0x5
0077f2: e6fc3600       mov r12,#0x36
0077fc: 8a0a0610       jb 0xfd14.0x1,0x00780c         -> 00780c
00780e: f78f92ef       movb 0xef92,0xff1e
007816: e7f8cc00       movb RL4,#0xcc
007820: f3f82eee       movb RL4,0xee2e
007834: f3f83aee       movb RL4,0xee3a
007848: 0e0d           bclr 0xfd1a.0x0
