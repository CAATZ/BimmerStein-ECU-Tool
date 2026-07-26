0001bc: 0aa90f00       bfldl 0xff52,#0x0,#0xf        
0001c0: f2f6cafa       mov r6,0xfaca                 
0001c4: 02f6d2fa       add r6,0xfad2                 
0001c8: 02f652fe       add r6,0xfe52                 
0001cc: f6f680fe       mov 0xfe80,r6                 
0001d0: 0ee0           bclr 0xffc0.0x0               
0001d2: 8ef0           bclr r0.0x8                   
0001d4: 8e02           bclr 0xfd04.0x8               
0001d6: 0aa90f0d       bfldl 0xff52,#0xd,#0xf        
0001da: 0d4f           jmpr cc_UC,0x00027a            -> 00027a
0001dc: 0aa9f000       bfldl 0xff52,#0x0,#0xf0       
0001e0: f2f6ccfa       mov r6,0xfacc                 
0001e4: 02f6d4fa       add r6,0xfad4                 
0001e8: 02f652fe       add r6,0xfe52                 
0001ec: f6f682fe       mov 0xfe82,r6                 
0001f0: 1ee0           bclr 0xffc0.0x1               
0001f2: 9ef0           bclr r0.0x9                   
0001f4: 9e02           bclr 0xfd04.0x9               
0001f6: 0aa9f0d0       bfldl 0xff52,#0xd0,#0xf0      
0001fa: 0d3f           jmpr cc_UC,0x00027a            -> 00027a
0001fc: 1aa9000f       bfldh 0xff52,#0xf,#0x0        
000200: f2f6cafa       mov r6,0xfaca                 
000204: 02f6d6fa       add r6,0xfad6                 
000208: 02f652fe       add r6,0xfe52                 
00020c: f6f684fe       mov 0xfe84,r6                 
000210: 2ee0           bclr 0xffc0.0x2               
000212: aef0           bclr r0.0xa                   
000214: ae02           bclr 0xfd04.0xa               
000216: 1aa90d0f       bfldh 0xff52,#0xf,#0xd        
00021a: 0d2f           jmpr cc_UC,0x00027a            -> 00027a
00021c: 1aa900f0       bfldh 0xff52,#0xf0,#0x0       
000220: f2f6ccfa       mov r6,0xfacc                 
000224: 02f6d8fa       add r6,0xfad8                 
000228: 02f652fe       add r6,0xfe52                 
00022c: f6f686fe       mov 0xfe86,r6                 
000230: 3ee0           bclr 0xffc0.0x3               
000232: bef0           bclr r0.0xb                   
000234: be02           bclr 0xfd04.0xb               
000236: 1aa9d0f0       bfldh 0xff52,#0xf0,#0xd0      
00023a: 0d1f           jmpr cc_UC,0x00027a            -> 00027a
00023c: 0aaa0f00       bfldl 0xff54,#0x0,#0xf        
000240: f2f6cafa       mov r6,0xfaca                 
000244: 02f6dafa       add r6,0xfada                 
000248: 02f652fe       add r6,0xfe52                 
00024c: f6f688fe       mov 0xfe88,r6                 
000250: 4ee0           bclr 0xffc0.0x4               
000252: cef0           bclr r0.0xc                   
000254: ce02           bclr 0xfd04.0xc               
000256: 0aaa0f0d       bfldl 0xff54,#0xd,#0xf        
00025a: 0d0f           jmpr cc_UC,0x00027a            -> 00027a
00025c: 0aaaf000       bfldl 0xff54,#0x0,#0xf0       
000260: f2f6ccfa       mov r6,0xfacc                 
000264: 02f6dcfa       add r6,0xfadc                 
000268: 02f652fe       add r6,0xfe52                 
00026c: f6f68afe       mov 0xfe8a,r6                 
000270: 5ee0           bclr 0xffc0.0x5               
000272: def0           bclr r0.0xd                   
000274: de02           bclr 0xfd04.0xd               
000276: 0aaaf0d0       bfldl 0xff54,#0xd0,#0xf0      
00028a: 0aa90f00       bfldl 0xff52,#0x0,#0xf        
00028e: 0fe0           bset 0xffc0.0x0               
000290: 0d13           jmpr cc_UC,0x0002b8            -> 0002b8
000292: 0aa9f000       bfldl 0xff52,#0x0,#0xf0       
000296: 1fe0           bset 0xffc0.0x1               
000298: 0d0f           jmpr cc_UC,0x0002b8            -> 0002b8
00029a: 1aa9000f       bfldh 0xff52,#0xf,#0x0        
00029e: 2fe0           bset 0xffc0.0x2               
0002a0: 0d0b           jmpr cc_UC,0x0002b8            -> 0002b8
0002a2: 1aa900f0       bfldh 0xff52,#0xf0,#0x0       
0002a6: 3fe0           bset 0xffc0.0x3               
0002a8: 0d07           jmpr cc_UC,0x0002b8            -> 0002b8
0002aa: 0aaa0f00       bfldl 0xff54,#0x0,#0xf        
0002ae: 4fe0           bset 0xffc0.0x4               
0002b0: 0d03           jmpr cc_UC,0x0002b8            -> 0002b8
0002b2: 0aaaf000       bfldl 0xff54,#0x0,#0xf0       
0002b6: 5fe0           bset 0xffc0.0x5               
001556: 43f2bcfa       cmpb RL1,0xfabc               
00155a: fd0c           jmpr cc_ULE,0x001574           -> 001574
00155c: e7f13900       movb RH0,#0x39                
001560: 06f16902       add r1,#0x269                 
001564: 47f27800       cmpb RL1,#0x78                
001568: eaf02e59       jmpa cc_ULE,0x00592e           -> 00592e
00156c: 27f27800       subb RL1,#0x78                
001570: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001574: e7f13b00       movb RH0,#0x3b                
001578: 06f10504       add r1,#0x405                 
00157c: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001580: 43f2bcfa       cmpb RL1,0xfabc               
001584: fd0c           jmpr cc_ULE,0x00159e           -> 00159e
001586: e7f13800       movb RH0,#0x38                
00158a: 06f16900       add r1,#0x69                  
00158e: 47f27800       cmpb RL1,#0x78                
001592: eaf02e59       jmpa cc_ULE,0x00592e           -> 00592e
001596: 27f27800       subb RL1,#0x78                
00159a: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00159e: e7f13900       movb RH0,#0x39                
0015a2: 06f10502       add r1,#0x205                 
0015aa: 43f2bcfa       cmpb RL1,0xfabc               
0015ae: fd0c           jmpr cc_ULE,0x0015c8           -> 0015c8
0015b0: e7f11800       movb RH0,#0x18                
0015b4: 06f1690a       add r1,#0xa69                 
0015b8: 47f27800       cmpb RL1,#0x78                
0015bc: eaf02e59       jmpa cc_ULE,0x00592e           -> 00592e
0015c0: 27f27800       subb RL1,#0x78                
0015c4: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0015c8: e7f13800       movb RH0,#0x38                
0015cc: 0815           add r1,#0x5                   
0015ce: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0015d2: 43f2bcfa       cmpb RL1,0xfabc               
0015d6: fd0b           jmpr cc_ULE,0x0015ee           -> 0015ee
0015d8: e181           movb RH0,#0x8                 
0015da: 06f16908       add r1,#0x869                 
0015de: 47f27800       cmpb RL1,#0x78                
0015e2: eaf02e59       jmpa cc_ULE,0x00592e           -> 00592e
0015e6: 27f27800       subb RL1,#0x78                
0015ea: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0015ee: e7f11800       movb RH0,#0x18                
0015f2: 06f1050a       add r1,#0xa05                 
0015f6: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0015fa: 43f2bcfa       cmpb RL1,0xfabc               
0015fe: fd0a           jmpr cc_ULE,0x001614           -> 001614
001600: 06f16906       add r1,#0x669                 
001604: 47f27800       cmpb RL1,#0x78                
001608: eaf02e59       jmpa cc_ULE,0x00592e           -> 00592e
00160c: 27f27800       subb RL1,#0x78                
001614: e181           movb RH0,#0x8                 
001616: 06f10508       add r1,#0x805                 
00161a: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00161e: 43f2bcfa       cmpb RL1,0xfabc               
001622: fd04           jmpr cc_ULE,0x00162c           -> 00162c
001624: 06f1800c       add r1,#0xc80                 
001628: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00162c: 06f10506       add r1,#0x605                 
001630: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001634: 43f2bcfa       cmpb RL1,0xfabc               
001638: fd06           jmpr cc_ULE,0x001646           -> 001646
00163a: e7f13300       movb RH0,#0x33                
00163e: 06f10504       add r1,#0x405                 
001642: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001646: e7f13700       movb RH0,#0x37                
00164a: 06f11906       add r1,#0x619                 
00164e: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001652: 43f2bcfa       cmpb RL1,0xfabc               
001656: fd06           jmpr cc_ULE,0x001664           -> 001664
001658: e7f13100       movb RH0,#0x31                
00165c: 06f10502       add r1,#0x205                 
001660: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001664: e7f13300       movb RH0,#0x33                
001668: 06f11904       add r1,#0x419                 
00166c: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001670: 43f2bcfa       cmpb RL1,0xfabc               
001674: fd05           jmpr cc_ULE,0x001680           -> 001680
001676: e7f13000       movb RH0,#0x30                
00167a: 0815           add r1,#0x5                   
00167c: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001680: e7f13100       movb RH0,#0x31                
001684: 06f11902       add r1,#0x219                 
001688: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00168c: 43f2bcfa       cmpb RL1,0xfabc               
001690: fd06           jmpr cc_ULE,0x00169e           -> 00169e
001692: e7f11000       movb RH0,#0x10                
001696: 06f1050a       add r1,#0xa05                 
00169a: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00169e: e7f13000       movb RH0,#0x30                
0016aa: 43f2bcfa       cmpb RL1,0xfabc               
0016ae: fd04           jmpr cc_ULE,0x0016b8           -> 0016b8
0016b0: 06f10508       add r1,#0x805                 
0016b4: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0016b8: e7f11000       movb RH0,#0x10                
0016bc: 06f1190a       add r1,#0xa19                 
0016c0: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0016c4: 43f2bcfa       cmpb RL1,0xfabc               
0016c8: fd04           jmpr cc_ULE,0x0016d2           -> 0016d2
0016ca: 06f1800c       add r1,#0xc80                 
0016d2: 06f11908       add r1,#0x819                 
0016d6: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0016da: 43f2bcfa       cmpb RL1,0xfabc               
0016de: fd06           jmpr cc_ULE,0x0016ec           -> 0016ec
0016e0: e7f12700       movb RH0,#0x27                
0016e4: 06f11906       add r1,#0x619                 
0016e8: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0016ec: e7f12f00       movb RH0,#0x2f                
0016f0: 06f12d08       add r1,#0x82d                 
0016f4: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0016f8: 43f2bcfa       cmpb RL1,0xfabc               
001716: 43f2bcfa       cmpb RL1,0xfabc               
001734: 43f2bcfa       cmpb RL1,0xfabc               
001738: fd06           jmpr cc_ULE,0x001746           -> 001746
00173a: e7f12000       movb RH0,#0x20                
00173e: 06f11900       add r1,#0x19                  
001742: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001746: e7f12100       movb RH0,#0x21                
00174a: 06f12d02       add r1,#0x22d                 
00174e: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001752: 43f2bcfa       cmpb RL1,0xfabc               
001756: fd04           jmpr cc_ULE,0x001760           -> 001760
001758: 06f1190a       add r1,#0xa19                 
00175c: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001760: e7f12000       movb RH0,#0x20                
001764: 06f12d00       add r1,#0x2d                  
001768: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00176c: 43f2bcfa       cmpb RL1,0xfabc               
001770: fd04           jmpr cc_ULE,0x00177a           -> 00177a
001772: 06f1800c       add r1,#0xc80                 
001776: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001782: 43f2bcfa       cmpb RL1,0xfabc               
001786: fd05           jmpr cc_ULE,0x001792           -> 001792
001788: e1f1           movb RH0,#0xf                 
00178a: 06f12d08       add r1,#0x82d                 
00178e: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001792: e7f11f00       movb RH0,#0x1f                
001796: 06f1410a       add r1,#0xa41                 
00179a: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00179e: 43f2bcfa       cmpb RL1,0xfabc               
0017a2: fd05           jmpr cc_ULE,0x0017ae           -> 0017ae
0017a4: e171           movb RH0,#0x7                 
0017a6: 06f12d06       add r1,#0x62d                 
0017aa: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0017ae: e1f1           movb RH0,#0xf                 
0017b0: 06f14108       add r1,#0x841                 
0017b8: 43f2bcfa       cmpb RL1,0xfabc               
0017bc: fd05           jmpr cc_ULE,0x0017c8           -> 0017c8
0017be: e131           movb RH0,#0x3                 
0017c0: 06f12d04       add r1,#0x42d                 
0017c4: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0017c8: e171           movb RH0,#0x7                 
0017ca: 06f14106       add r1,#0x641                 
0017ce: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0017d2: 43f2bcfa       cmpb RL1,0xfabc               
0017d6: fd05           jmpr cc_ULE,0x0017e2           -> 0017e2
0017d8: e111           movb RH0,#0x1                 
0017da: 06f12d02       add r1,#0x22d                 
0017de: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0017e2: e131           movb RH0,#0x3                 
0017e4: 06f14104       add r1,#0x441                 
0017e8: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
0017ec: 43f2bcfa       cmpb RL1,0xfabc               
00181a: 43f2bcfa       cmpb RL1,0xfabc               
00181e: fd06           jmpr cc_ULE,0x00182c           -> 00182c
001820: e7f11e00       movb RH0,#0x1e                
001824: 06f1410a       add r1,#0xa41                 
001828: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
00182c: e7f13e00       movb RH0,#0x3e                
001830: 06f15500       add r1,#0x55                  
001834: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001838: 43f2bcfa       cmpb RL1,0xfabc               
00183c: fd05           jmpr cc_ULE,0x001848           -> 001848
00183e: e1e1           movb RH0,#0xe                 
001840: 06f14108       add r1,#0x841                 
001844: ea002e59       jmpa cc_UC,0x00592e            -> 00592e
001848: e7f11e00       movb RH0,#0x1e                
00186c: 43f2bcfa       cmpb RL1,0xfabc               
001870: fd04           jmpr cc_ULE,0x00187a           -> 00187a
001872: e121           movb RH0,#0x2                 
001874: 06f14104       add r1,#0x441                 
001878: 0d5a           jmpr cc_UC,0x00192e            -> 00192e
00187a: e161           movb RH0,#0x6                 
00187c: 06f15506       add r1,#0x655                 
001880: 0d56           jmpr cc_UC,0x00192e            -> 00192e
001882: 43f2bcfa       cmpb RL1,0xfabc               
001886: fd03           jmpr cc_ULE,0x00188e           -> 00188e
001888: 06f14102       add r1,#0x241                 
00188c: 0d50           jmpr cc_UC,0x00192e            -> 00192e
00188e: e121           movb RH0,#0x2                 
001890: 06f15504       add r1,#0x455                 
001894: 0d4c           jmpr cc_UC,0x00192e            -> 00192e
001896: 43f2bcfa       cmpb RL1,0xfabc               
00189a: fd03           jmpr cc_ULE,0x0018a2           -> 0018a2
00189c: 06f1800c       add r1,#0xc80                 
0018a0: 0d46           jmpr cc_UC,0x00192e            -> 00192e
0018a2: 06f15502       add r1,#0x255                 
0018a6: 0d43           jmpr cc_UC,0x00192e            -> 00192e
0018a8: 43f2bcfa       cmpb RL1,0xfabc               
0018ac: fd05           jmpr cc_ULE,0x0018b8           -> 0018b8
0018ae: e7f13c00       movb RH0,#0x3c                
0018b2: 06f15500       add r1,#0x55                  
0018b6: 0d3b           jmpr cc_UC,0x00192e            -> 00192e
0018b8: e7f13d00       movb RH0,#0x3d                
0018bc: 06f16902       add r1,#0x269                 
0018c0: 0d36           jmpr cc_UC,0x00192e            -> 00192e
0018c2: 43f2bcfa       cmpb RL1,0xfabc               
0018c6: fd05           jmpr cc_ULE,0x0018d2           -> 0018d2
0018d2: e7f13c00       movb RH0,#0x3c                
0018d6: 06f16900       add r1,#0x69                  
0018da: 0d29           jmpr cc_UC,0x00192e            -> 00192e
0018dc: 43f2bcfa       cmpb RL1,0xfabc               
0018e0: fd04           jmpr cc_ULE,0x0018ea           -> 0018ea
0018e2: e1c1           movb RH0,#0xc                 
0018e4: 06f15508       add r1,#0x855                 
0018e8: 0d22           jmpr cc_UC,0x00192e            -> 00192e
0018ea: e7f11c00       movb RH0,#0x1c                
0018ee: 06f1690a       add r1,#0xa69                 
0018f2: 0d1d           jmpr cc_UC,0x00192e            -> 00192e
0018f4: 43f2bcfa       cmpb RL1,0xfabc               
0018f8: fd04           jmpr cc_ULE,0x001902           -> 001902
0018fa: e141           movb RH0,#0x4                 
0018fc: 06f15506       add r1,#0x655                 
001900: 0d16           jmpr cc_UC,0x00192e            -> 00192e
001902: e1c1           movb RH0,#0xc                 
001904: 06f16908       add r1,#0x869                 
001908: 0d12           jmpr cc_UC,0x00192e            -> 00192e
00190a: 43f2bcfa       cmpb RL1,0xfabc               
00190e: fd03           jmpr cc_ULE,0x001916           -> 001916
001910: 06f15504       add r1,#0x455                 
001914: 0d0c           jmpr cc_UC,0x00192e            -> 00192e
001916: e141           movb RH0,#0x4                 
001918: 06f16906       add r1,#0x669                 
00191c: 0d08           jmpr cc_UC,0x00192e            -> 00192e
00191e: 43f2bcfa       cmpb RL1,0xfabc               
001922: fd03           jmpr cc_ULE,0x00192a           -> 00192a
001924: 06f1800c       add r1,#0xc80                 
001928: 0d02           jmpr cc_UC,0x00192e            -> 00192e
00192a: 06f16904       add r1,#0x469                 
