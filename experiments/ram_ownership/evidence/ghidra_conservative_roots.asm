00736a: f3f832f7       movb RL4,0xf732
00736e: 47f85500       cmpb RL4,#0x55
007372: 2d19           jmpr cc_EQ,0x0073a6        -> 0073a6
007374: f3fafee9       movb RL5,0xe9fe
007378: 47faff00       cmpb RL5,#0xff
00737c: 2d02           jmpr cc_EQ,0x007382        -> 007382
00737e: da02b23f       calls 0x023fb2             -> 023fb2
007f00: e6f794f7       mov r7,#0xf794
007f04: e008           mov r8,#0x0
007f06: a897           mov r9,[r7]
007f08: 2d09           jmpr cc_EQ,0x007f1c        -> 007f1c
007f0a: 2891           sub r9,#0x1
007f0c: b897           mov [r7],r9
007f0e: 4890           cmp r9,#0x0
007f10: 3d05           jmpr cc_NE,0x007f1c        -> 007f1c
007f12: f048           mov r4,r8
007f14: 5c14           shl r4,#0x1
007f16: d45454ad       mov r5,[r4+#0xad54]
007f1a: ab05           calli cc_UC,[r5]
007f1c: 0872           add r7,#0x2
007f1e: 0881           add r8,#0x1
007f20: 46f81f00       cmp r8,#0x1f
007f24: 8df0           jmpr cc_C,0x007f06         -> 007f06
022bc0: f3f894f6       movb RL4,0xf694               
022bc4: f7f876f4       movb 0xf476,RL4               
022bc8: f3fa9ef4       movb RL5,0xf49e               
022bcc: f7fa77f4       movb 0xf477,RL5               
022bd0: e6fc76f4       mov r12,#0xf476               
022bd4: e02d           mov r13,#0x2                  
022bd6: da02b259       calls 0x0259b2                 -> 0259b2
022bda: f6f478f4       mov 0xf478,r4                 
022bde: db00           rets                          
022f22: e7f87f00       movb RL4,#0x7f                
022f26: 65f8e4f6       andb 0xf6e4,RL4               
022f2a: e118           movb RL4,#0x1                 
022f2c: 75f8e5f6       orb 0xf6e5,RL4                
022f30: f68ec2f7       mov 0xf7c2,0xff1c             
022f34: 6e05           bclr 0xfd0a.0x6               
022f36: 0d03           jmpr cc_UC,0x022f3e            -> 022f3e
022f3e: e6fc72ec       mov r12,#0xec72               
022f42: e6fdeca8       mov r13,#0xa8ec               
022f46: c2fe2102       movbz r14,0x221               
022f4a: c2ff4802       movbz r15,0x248               
022f4e: da02207d       calls 0x027d20                 -> 027d20
022f52: 4a1c1b62       bmov 0xfd36.0x2,0xfd38.0x6    
022f56: c2f47cec       movbz r4,0xec7c               
022f5a: 66f42000       and r4,#0x20                  
022f5e: 2d04           jmpr cc_EQ,0x022f68            -> 022f68
022f60: e6f4fbff       mov r4,#0xfffb                
022f64: 64f4f8f5       and 0xf5f8,r4                 
022f68: 0802           add r0,#0x2                   
022f6a: 9890           mov r9,[r0+]                  
022f6c: 9870           mov r7,[r0+]                  
022f6e: 9860           mov r6,[r0+]                  
022f70: db00           rets                          
02384e: 47f5ea00       cmpb RH2,#0xea                
023854: f48d0a00       movb RL4,[r13+#0xa]           
023858: c084           movbz r4,RL4                  
02385a: 6844           and r4,#0x4                   
02385c: 3d72           jmpr cc_NE,0x023942            -> 023942
02385e: e00c           mov r12,#0x0                  
023860: f04c           mov r4,r12                    
023862: c084           movbz r4,RL4                  
023864: e6f547f5       mov r5,#0xf547                
023868: 0054           add r5,r4                     
02386a: a985           movb RL4,[r5]                 
02386c: 63f8dbab       andb RL4,0xabdb               
023870: b985           movb [r5],RL4                 
023872: f04c           mov r4,r12                    
023874: 0981           addb RL4,#0x1                 
023876: f0c4           mov r12,r4                    
023878: f04c           mov r4,r12                    
02387a: 4986           cmpb RL4,#0x6                 
02387c: 8df1           jmpr cc_C,0x023860             -> 023860
02387e: f78e48f5       movb 0xf548,0xff1c            
023882: 0d5f           jmpr cc_UC,0x023942            -> 023942
0238f0: f04c           mov r4,r12                    
0238f2: c084           movbz r4,RL4                  
0238f4: e6f547f5       mov r5,#0xf547                
0238f8: 0054           add r5,r4                     
0238fa: a985           movb RL4,[r5]                 
0238fc: 63f8deab       andb RL4,0xabde               
023900: b985           movb [r5],RL4                 
023902: f04c           mov r4,r12                    
023904: 0981           addb RL4,#0x1                 
023906: f0c4           mov r12,r4                    
023908: f04c           mov r4,r12                    
02390a: 4986           cmpb RL4,#0x6                 
02390c: 8df1           jmpr cc_C,0x0238f0             -> 0238f0
02390e: f78e4bf5       movb 0xf54b,0xff1c            
023912: 0d17           jmpr cc_UC,0x023942            -> 023942
023942: f04e           mov r4,r14                    
023944: 0981           addb RL4,#0x1                 
023946: f0e4           mov r14,r4                    
023948: f04e           mov r4,r14                    
02394a: c084           movbz r4,RL4                  
02394c: 46f45f00       cmp r4,#0x5f                  
023950: eac0e877       jmpa cc_SLT,0x0277e8           -> 0277e8
023954: db00           rets                          
023a50: 0600d447       add 0xfe00,#0x47d4            
023a54: 0e00           bclr 0xfd00.0x0               
023a56: a9a4           movb RL5,[r4]                 
023a58: e4a80700       movb [r8+#0x7],RL5            
023a5c: c2f40eea       movbz r4,0xea0e               
023a60: f4a70100       movb RL5,[r7+#0x1]            
023a64: c0a5           movbz r5,RL5                  
023a66: 66f51000       and r5,#0x10                  
023a6a: 7045           or r4,r5                      
023a6c: f6f438fd       mov 0xfd38,r4                 
023a70: f3f838fd       movb RL4,0xfd38               
023a74: b988           movb [r8],RL4                 
023a76: f3f850ef       movb RL4,0xef50               
023a7a: e4880900       movb [r8+#0x9],RL4            
023a7e: f3f851ef       movb RL4,0xef51               
023a82: e4880800       movb [r8+#0x8],RL4            
023a86: f4800800       movb RL4,[r0+#0x8]            
023a8a: c084           movbz r4,RL4                  
023a8c: 0049           add r4,r9                     
023a8e: f094           mov r9,r4                     
023a90: c0c4           movbz r4,RL6                  
023a92: 4094           cmp r9,r4                     
023a94: 8d38           jmpr cc_C,0x023b06             -> 023b06
023a96: c0c9           movbz r9,RL6                  
023a98: 8a1c0d50       jb 0xfd38.0x5,0x023ab6         -> 023ab6
023a9c: f047           mov r4,r7                     
023a9e: e6f2cca5       mov r2,#0xa5cc                
023aa2: 2042           sub r4,r2                     
023aa4: 7c44           shr r4,#0x4                   
023aa6: c2f518ea       movbz r5,0xea18               
023aaa: e48592ee       movb [r5+#0xee92],RL4         
023aae: e118           movb RL4,#0x1                 
023ab0: 05f818ea       addb 0xea18,RL4               
023ab4: 5f1c           bset 0xfd38.0x5               
023ab6: 6f1c           bset 0xfd38.0x6               
023ab8: f3f838fd       movb RL4,0xfd38               
023abc: b988           movb [r8],RL4                 
023abe: f4870100       movb RL4,[r7+#0x1]            
023ac2: 67f80f00       andb RL4,#0xf                 
023ac6: 2d04           jmpr cc_EQ,0x023ad0            -> 023ad0
023ac8: f0c8           mov r12,r8                    
023aca: f0d7           mov r13,r7                    
023acc: da021077       calls 0x027710                 -> 027710
023ad0: e7f82800       movb RL4,#0x28                
023ad4: e4880300       movb [r8+#0x3],RL4            
023ad8: f4880200       movb RL4,[r8+#0x2]            
023adc: 47f8ff00       cmpb RL4,#0xff                
023ae0: 9d12           jmpr cc_NC,0x023b06            -> 023b06
023ae2: f048           mov r4,r8                     
023ae4: 0842           add r4,#0x2                   
023ae6: a9a4           movb RL5,[r4]                 
023ae8: 09a1           addb RL5,#0x1                 
023aea: b9a4           movb [r4],RL5                 
023aec: 0d0c           jmpr cc_UC,0x023b06            -> 023b06
023b06: f049           mov r4,r9                     
023b08: e4880100       movb [r8+#0x1],RL4            
023b0c: f78e0eea       movb 0xea0e,0xff1c            
023b10: 9890           mov r9,[r0+]                  
023b12: 9880           mov r8,[r0+]                  
023b14: 9870           mov r7,[r0+]                  
023b16: 9860           mov r6,[r0+]                  
023b18: 0802           add r0,#0x2                   
023b1a: db00           rets                          
023fa6: f049           mov r4,r9                     
023fa8: 06f40a00       add r4,#0xa                   
023fac: a9a4           movb RL5,[r4]                 
023fae: 67fa7000       andb RL5,#0x70                
023fb2: b9a4           movb [r4],RL5                 
023fb4: c0c4           movbz r4,RL6                  
023fb6: f4a492ee       movb RL5,[r4+#0xee92]         
023fba: 43fa3cf6       cmpb RL5,0xf63c               
023fbe: 3d05           jmpr cc_NE,0x023fca            -> 023fca
023fc0: 4e2c           bclr 0xfd58.0x4               
023fc2: 5e2c           bclr 0xfd58.0x5               
023fc4: 6e2c           bclr 0xfd58.0x6               
023fc6: f78e3df6       movb 0xf63d,0xff1c            
023fca: c0c4           movbz r4,RL6                  
023fcc: f4a492ee       movb RL5,[r4+#0xee92]         
023fd0: c0ac           movbz r12,RL5                 
023fd2: da037012       calls 0x031270                 -> 031270
023fd6: f1e8           movb RL7,RL4                  
023fd8: 47f8ff00       cmpb RL4,#0xff                
023fdc: 2d0c           jmpr cc_EQ,0x023ff6            -> 023ff6
023fde: c0e4           movbz r4,RL7                  
023fe0: f054           mov r5,r4                     
023fe2: 5c25           shl r5,#0x2                   
023fe4: 2054           sub r5,r4                     
023fe6: 5c15           shl r5,#0x1                   
023fe8: e6f442f6       mov r4,#0xf642                
023fec: 0045           add r4,r5                     
023fee: a9a4           movb RL5,[r4]                 
023ff0: 67facf00       andb RL5,#0xcf                
023ff4: b9a4           movb [r4],RL5                 
023ff6: c0c4           movbz r4,RL6                  
023ff8: f4a492ee       movb RL5,[r4+#0xee92]         
023ffc: c0ac           movbz r12,RL5                 
023ffe: da039a2e       calls 0x032e9a                 -> 032e9a
024002: 58d0           xor r13,#0x0                  
024004: f3f8fee9       movb RL4,0xe9fe               
024008: 47f83400       cmpb RL4,#0x34                
02400c: 2d53           jmpr cc_EQ,0x0240b4            -> 0240b4
02400e: c2f46e03       movbz r4,0x36e                
024012: f2f5c8e8       mov r5,0xe8c8                 
024016: 4054           cmp r5,r4                     
024018: 8d48           jmpr cc_C,0x0240aa             -> 0240aa
02401a: 8a0a4600       jb 0xfd14.0x0,0x0240aa         -> 0240aa
02401e: 8a0a4410       jb 0xfd14.0x1,0x0240aa         -> 0240aa
024022: f3f89af1       movb RL4,0xf19a               
024026: 43f85403       cmpb RL4,0x354                
02402a: 8d34           jmpr cc_C,0x024094             -> 024094
02402c: e6fc0215       mov r12,#0x1502               
024030: c2fd3cfc       movbz r13,0xfc3c              
024034: da03ac48       calls 0x0348ac                 -> 0348ac
024038: e6fc0015       mov r12,#0x1500               
02403c: c2fd94f6       movbz r13,0xf694              
024040: da03fc48       calls 0x0348fc                 -> 0348fc
024044: e6fc0415       mov r12,#0x1504               
024048: da036a4a       calls 0x034a6a                 -> 034a6a
02404c: f7f8f1e8       movb 0xe8f1,RL4               
024050: 9a040b00       jnb 0xfd08.0x0,0x02406a        -> 02406a
024054: f3fad0e8       movb RL5,0xe8d0               
024058: 43faf1e8       cmpb RL5,0xe8f1               
02405c: fd2b           jmpr cc_ULE,0x0240b4           -> 0240b4
02405e: e7f64000       movb RL3,#0x40                
024062: 75f6e5f6       orb 0xf6e5,RL3                
024066: 0e04           bclr 0xfd08.0x0               
024068: db00           rets                          
02406a: c2fcf1e8       movbz r12,0xe8f1              
02406e: c2fd0f02       movbz r13,0x20f               
024072: da031a44       calls 0x03441a                 -> 03441a
024076: f3fad0e8       movb RL5,0xe8d0               
02407a: 41a8           cmpb RL5,RL4                  
02407c: 9d1b           jmpr cc_NC,0x0240b4            -> 0240b4
02407e: f2f410ea       mov r4,0xea10                 
024082: 66f42000       and r4,#0x20                  
024086: 3d04           jmpr cc_NE,0x024090            -> 024090
024088: e7f8bf00       movb RL4,#0xbf                
02408c: 65f8e5f6       andb 0xf6e5,RL4               
024090: 0f04           bset 0xfd08.0x0               
024092: db00           rets                          
024094: f2f410ea       mov r4,0xea10                 
024098: 66f42000       and r4,#0x20                  
02409c: 3d04           jmpr cc_NE,0x0240a6            -> 0240a6
02409e: e7f8bf00       movb RL4,#0xbf                
0240a2: 65f8e5f6       andb 0xf6e5,RL4               
0240a6: 0f04           bset 0xfd08.0x0               
0240a8: db00           rets                          
0240aa: e7f84000       movb RL4,#0x40                
0240ae: 75f8e5f6       orb 0xf6e5,RL4                
0240b2: 0e04           bclr 0xfd08.0x0               
0240b4: db00           rets                          
0259b2: 2d22           jmpr cc_EQ,0x0259f8            -> 0259f8
0259b4: e118           movb RL4,#0x1                 
0259b6: f7f80eea       movb 0xea0e,RL4               
0259ba: e6fceeeb       mov r12,#0xebee               
0259be: e6fd3ca8       mov r13,#0xa83c               
0259c2: c2fe1a02       movbz r14,0x21a               
0259c6: c2ff4102       movbz r15,0x241               
0259ca: da02207d       calls 0x027d20                 -> 027d20
0259ce: 4a1c1a67       bmov 0xfd34.0x7,0xfd38.0x6    
0259d2: c2f4f8eb       movbz r4,0xebf8               
0259d6: 66f42000       and r4,#0x20                  
0259da: 2d04           jmpr cc_EQ,0x0259e4            -> 0259e4
0259dc: e6f47fff       mov r4,#0xff7f                
0259e0: 64f4f6f5       and 0xf5f6,r4                 
0259e4: e6f42000       mov r4,#0x20                  
0259e8: 74f410ea       or 0xea10,r4                  
0259ec: e7f84000       movb RL4,#0x40                
0259f0: 75f8e5f6       orb 0xf6e5,RL4                
0259f4: ea00c02b       jmpa cc_UC,0x022bc0            -> 022bc0
0259f8: f3f8f9f6       movb RL4,0xf6f9               
0259fc: 67f82000       andb RL4,#0x20                
025a00: 2d1b           jmpr cc_EQ,0x025a38            -> 025a38
025a02: e128           movb RL4,#0x2                 
025a04: f7f80eea       movb 0xea0e,RL4               
025a08: e6fceeeb       mov r12,#0xebee               
025a0c: e6fd3ca8       mov r13,#0xa83c               
025a10: c2fe1a02       movbz r14,0x21a               
025a14: c2ff4102       movbz r15,0x241               
025a18: da02207d       calls 0x027d20                 -> 027d20
025a1c: 4a1c1a67       bmov 0xfd34.0x7,0xfd38.0x6    
025a20: c2f4f8eb       movbz r4,0xebf8               
025a24: 66f42000       and r4,#0x20                  
025a28: ea20c02b       jmpa cc_EQ,0x022bc0            -> 022bc0
025a2c: e6f47fff       mov r4,#0xff7f                
025a30: 64f4f6f5       and 0xf5f6,r4                 
025a34: ea00c02b       jmpa cc_UC,0x022bc0            -> 022bc0
025a38: f3f8e5f6       movb RL4,0xf6e5               
025a3c: 67f84000       andb RL4,#0x40                
025a40: 2d05           jmpr cc_EQ,0x025a4c            -> 025a4c
025a42: c2f4eeeb       movbz r4,0xebee               
025a46: 6842           and r4,#0x2                   
025a48: ea20c02b       jmpa cc_EQ,0x022bc0            -> 022bc0
025a4c: e6fceeeb       mov r12,#0xebee               
025a50: e6fd3ca8       mov r13,#0xa83c               
025a54: c2fe1a02       movbz r14,0x21a               
025a58: c2ff4102       movbz r15,0x241               
025a5c: da02207d       calls 0x027d20                 -> 027d20
025a60: 4a1c1a67       bmov 0xfd34.0x7,0xfd38.0x6    
025a64: c2f4f8eb       movbz r4,0xebf8               
025a68: 66f42000       and r4,#0x20                  
025a6c: ea20c02b       jmpa cc_EQ,0x022bc0            -> 022bc0
025a70: e6f47fff       mov r4,#0xff7f                
025a74: 64f4f6f5       and 0xf5f6,r4                 
025a78: ea00c02b       jmpa cc_UC,0x022bc0            -> 022bc0
026410: f3f805f7       movb RL4,0xf705               
026414: 67f88000       andb RL4,#0x80                
026418: ea20c02b       jmpa cc_EQ,0x022bc0            -> 022bc0
02641c: 0fe2           bset 0xffc4.0x0               
02641e: ea00c02b       jmpa cc_UC,0x022bc0            -> 022bc0
0270f4: 3e12           bclr 0xfd24.0x3               
0270f6: 0e20           bclr 0xfd40.0x0               
0270f8: e6fc1a19       mov r12,#0x191a               
0270fc: c2fd3cfc       movbz r13,0xfc3c              
027100: da03024b       calls 0x034b02                 -> 034b02
027104: e6fc1c19       mov r12,#0x191c               
027108: da03a64b       calls 0x034ba6                 -> 034ba6
02710c: f3fad6e8       movb RL5,0xe8d6               
027110: 41a8           cmpb RL5,RL4                  
027112: 8d01           jmpr cc_C,0x027116             -> 027116
027114: 6f12           bset 0xfd24.0x6               
027116: f3f8d6e8       movb RL4,0xe8d6               
02711a: 43f80e02       cmpb RL4,0x20e                
02711e: 9d02           jmpr cc_NC,0x027124            -> 027124
027120: 7f12           bset 0xfd24.0x7               
027122: 0d01           jmpr cc_UC,0x027126            -> 027126
027124: 7e12           bclr 0xfd24.0x7               
027126: e6fcd418       mov r12,#0x18d4               
02712a: c2fd3cfc       movbz r13,0xfc3c              
02712e: da03024b       calls 0x034b02                 -> 034b02
027132: e6fcd618       mov r12,#0x18d6               
027136: da03a64b       calls 0x034ba6                 -> 034ba6
02713a: f3fad6e8       movb RL5,0xe8d6               
02713e: 41a8           cmpb RL5,RL4                  
027140: 8d07           jmpr cc_C,0x027150             -> 027150
027142: 5f12           bset 0xfd24.0x5               
027144: e6fc2c19       mov r12,#0x192c               
027148: da03a64b       calls 0x034ba6                 -> 034ba6
02714c: f7f8b8e9       movb 0xe9b8,RL4               
027150: 9a0c0b60       jnb 0xfd18.0x6,0x02716a        -> 02716a
027154: f3f8d6e8       movb RL4,0xe8d6               
027158: 43f80c02       cmpb RL4,0x20c                
02715c: fd10           jmpr cc_ULE,0x02717e           -> 02717e
02715e: 7f04           bset 0xfd08.0x7               
027160: c2f45a03       movbz r4,0x35a                
027164: f6f4c4f7       mov 0xf7c4,r4                 
027168: 0d0a           jmpr cc_UC,0x02717e            -> 02717e
02716a: f3f8d6e8       movb RL4,0xe8d6               
02716e: 43f80b02       cmpb RL4,0x20b                
027172: fd05           jmpr cc_ULE,0x02717e           -> 02717e
027174: 7f04           bset 0xfd08.0x7               
027176: c2f45a03       movbz r4,0x35a                
02717a: f6f4c4f7       mov 0xf7c4,r4                 
02717e: e6fcea2e       mov r12,#0x2eea               
027182: c2fdd0e8       movbz r13,0xe8d0              
027186: da03ac48       calls 0x0348ac                 -> 0348ac
02718a: e6fcec2e       mov r12,#0x2eec               
02718e: da03344a       calls 0x034a34                 -> 034a34
027192: f3fad6e8       movb RL5,0xe8d6               
027196: 41a8           cmpb RL5,RL4                  
027198: fd0a           jmpr cc_ULE,0x0271ae           -> 0271ae
02719a: f2f47ef7       mov r4,0xf77e                 
02719e: 42f48c00       cmp r4,0x8c                   
0271a2: 9d05           jmpr cc_NC,0x0271ae            -> 0271ae
0271a4: f2f58c00       mov r5,0x8c                   
0271a8: f6f57ef7       mov 0xf77e,r5                 
0271ac: 8f29           bset 0xfd52.0x8               
0271ae: 9860           mov r6,[r0+]                  
0271b0: db00           rets                          
0276d8: f3f822e4       movb RL4,0xe422               
0276dc: 4984           cmpb RL4,#0x4                 
0276de: 2d03           jmpr cc_EQ,0x0276e6            -> 0276e6
0276e0: 47f82400       cmpb RL4,#0x24                
0276e4: 3d18           jmpr cc_NE,0x027716            -> 027716
0276e6: f04d           mov r4,r13                    
0276e8: c084           movbz r4,RL4                  
0276ea: f4a492ee       movb RL5,[r4+#0xee92]         
0276ee: c0a4           movbz r4,RL5                  
0276f0: f054           mov r5,r4                     
0276f2: 5c25           shl r5,#0x2                   
0276f4: 2054           sub r5,r4                     
0276f6: 5c25           shl r5,#0x2                   
0276f8: e6fe1aea       mov r14,#0xea1a               
0276fc: 00e5           add r14,r5                    
0276fe: f04d           mov r4,r13                    
027700: 08d1           add r13,#0x1                  
027702: c084           movbz r4,RL4                  
027704: f4a492ee       movb RL5,[r4+#0xee92]         
027708: c0a4           movbz r4,RL5                  
02770a: 5c44           shl r4,#0x4                   
02770c: f4a4cca5       movb RL5,[r4+#0xa5cc]         
027710: b9ac           movb [r12],RL5                
027712: 08c1           add r12,#0x1                  
027714: 0d17           jmpr cc_UC,0x027744            -> 027744
027716: f04d           mov r4,r13                    
027718: c084           movbz r4,RL4                  
02771a: f4a4f1ee       movb RL5,[r4+#0xeef1]         
02771e: c0a4           movbz r4,RL5                  
027720: f054           mov r5,r4                     
027722: 5c25           shl r5,#0x2                   
027724: 2054           sub r5,r4                     
027726: 5c25           shl r5,#0x2                   
027728: e6fe1aea       mov r14,#0xea1a               
02772c: 00e5           add r14,r5                    
02772e: f04d           mov r4,r13                    
027730: 08d1           add r13,#0x1                  
027732: c084           movbz r4,RL4                  
027734: f4a4f1ee       movb RL5,[r4+#0xeef1]         
027738: c0a4           movbz r4,RL5                  
02773a: 5c44           shl r4,#0x4                   
02773c: f4a4cca5       movb RL5,[r4+#0xa5cc]         
027740: b9ac           movb [r12],RL5                
027742: 08c1           add r12,#0x1                  
027744: e00f           mov r15,#0x0                  
027746: 0d17           jmpr cc_UC,0x027776            -> 027776
027748: e6f21aea       mov r2,#0xea1a                
02774c: e6f41bea       mov r4,#0xea1b                
027750: 2042           sub r4,r2                     
027752: f05f           mov r5,r15                    
027754: c0a5           movbz r5,RL5                  
027756: 4054           cmp r5,r4                     
027758: 3d08           jmpr cc_NE,0x02776a            -> 02776a
02775a: f3f822e4       movb RL4,0xe422               
02775e: 47f81400       cmpb RL4,#0x14                
027762: 3d02           jmpr cc_NE,0x027768            -> 027768
027764: c9ce           movb [r12],[r14]              
027766: 08c1           add r12,#0x1                  
027768: 08e1           add r14,#0x1                  
02776a: c9ce           movb [r12],[r14]              
02776c: 08c1           add r12,#0x1                  
02776e: 08e1           add r14,#0x1                  
027770: f04f           mov r4,r15                    
027772: 0981           addb RL4,#0x1                 
027774: f0f4           mov r15,r4                    
027776: c0e4           movbz r4,RL7                  
027778: 2841           sub r4,#0x1                   
02777a: f05f           mov r5,r15                    
02777c: c0a5           movbz r5,RL5                  
02777e: 4054           cmp r5,r4                     
027780: cde3           jmpr cc_SLT,0x027748           -> 027748
027782: f04d           mov r4,r13                    
027784: 418c           cmpb RL4,RL6                  
027786: 9d0a           jmpr cc_NC,0x02779c            -> 02779c
027788: c0e4           movbz r4,RL7                  
02778a: e6f50001       mov r5,#0x100                 
02778e: 2054           sub r5,r4                     
027790: 2855           sub r5,#0x5                   
027792: e6f420e5       mov r4,#0xe520                
027796: 0045           add r4,r5                     
027798: 40c4           cmp r12,r4                    
02779a: 8d9e           jmpr cc_C,0x0276d8             -> 0276d8
02779c: e6f420e5       mov r4,#0xe520                
0277a0: f05c           mov r5,r12                    
0277a2: 2054           sub r5,r4                     
0277a4: 0851           add r5,#0x1                   
0277a6: f7fa21e5       movb 0xe521,RL5               
0277aa: 9870           mov r7,[r0+]                  
0277ac: 9860           mov r6,[r0+]                  
0277ae: db00           rets                          
0277e8: f2f450ef       mov r4,0xef50                 
0277ec: f6f48eee       mov 0xee8e,r4                 
0277f0: f68e30fd       mov 0xfd30,0xff1c             
0277f4: f68e32fd       mov 0xfd32,0xff1c             
0277f8: f68e34fd       mov 0xfd34,0xff1c             
0277fc: f68e36fd       mov 0xfd36,0xff1c             
027800: f68e0cea       mov 0xea0c,0xff1c             
027804: f68e4cfd       mov 0xfd4c,0xff1c             
027808: f68e4efd       mov 0xfd4e,0xff1c             
02780c: f68e50fd       mov 0xfd50,0xff1c             
027810: f78ea1fc       movb 0xfca1,0xff1c            
027814: 9e22           bclr 0xfd44.0x9               
027816: ae22           bclr 0xfd44.0xa               
027818: f78e44f5       movb 0xf544,0xff1c            
02781c: f3f844f5       movb RL4,0xf544               
027820: 73f8f9e9       orb RL4,0xe9f9                
027824: f7f835fc       movb 0xfc35,RL4               
027828: f78e1af5       movb 0xf51a,0xff1c            
02782c: da039011       calls 0x031190                 -> 031190
027830: da02eec2       calls 0x02c2ee                 -> 02c2ee
027834: da026a38       calls 0x02386a                 -> 02386a
027838: 9890           mov r9,[r0+]                  
02783a: 9880           mov r8,[r0+]                  
02783c: db00           rets                          
027956: 66f9ff03       and r9,#0x3ff                 
02795a: ea00503a       jmpa cc_UC,0x023a50            -> 023a50
027afc: a986           movb RL4,[r6]                 
027afe: 2d05           jmpr cc_EQ,0x027b0a            -> 027b0a
027b00: 4981           cmpb RL4,#0x1                 
027b02: 2d03           jmpr cc_EQ,0x027b0a            -> 027b0a
027b04: 4982           cmpb RL4,#0x2                 
027b06: 2d2a           jmpr cc_EQ,0x027b5c            -> 027b5c
027b08: 0d36           jmpr cc_UC,0x027b76            -> 027b76
027b0a: f2f900fe       mov r9,0xfe00                 
027b0e: f4860200       movb RL4,[r6+#0x2]            
027b12: c084           movbz r4,RL4                  
027b14: 6843           and r4,#0x3                   
027b16: 5c24           shl r4,#0x2                   
027b18: f4a60300       movb RL5,[r6+#0x3]            
027b1c: c0a5           movbz r5,RL5                  
027b1e: 66f5c000       and r5,#0xc0                  
027b22: bc65           ashr r5,#0x6                  
027b24: 7045           or r4,r5                      
027b26: f6f400fe       mov 0xfe00,r4                 
027b2a: f087           mov r8,r7                     
027b2c: f4860300       movb RL4,[r6+#0x3]            
027b30: c088           movbz r8,RL4                  
027b32: 5c88           shl r8,#0x8                   
027b34: f4860400       movb RL4,[r6+#0x4]            
027b38: c084           movbz r4,RL4                  
027b3a: 7084           or r8,r4                      
027b3c: 66f8ff3f       and r8,#0x3fff                
027b40: a986           movb RL4,[r6]                 
027b42: 3d02           jmpr cc_NE,0x027b48            -> 027b48
027b44: c978           movb [r7],[r8]                
027b46: 0d05           jmpr cc_UC,0x027b52            -> 027b52
027b48: f4880100       movb RL4,[r8+#0x1]            
027b4c: b987           movb [r7],RL4                 
027b4e: 0871           add r7,#0x1                   
027b50: c978           movb [r7],[r8]                
027b52: 0871           add r7,#0x1                   
027b54: f6f900fe       mov 0xfe00,r9                 
027b58: f087           mov r8,r7                     
027b5a: 0d0d           jmpr cc_UC,0x027b76            -> 027b76
027b5c: f4860400       movb RL4,[r6+#0x4]            
027b60: c08c           movbz r12,RL4                 
027b62: da02f238       calls 0x0238f2                 -> 0238f2
027b66: f094           mov r9,r4                     
027b68: f049           mov r4,r9                     
027b6a: e4870100       movb [r7+#0x1],RL4            
027b6e: f049           mov r4,r9                     
027b70: 7c84           shr r4,#0x8                   
027b72: b987           movb [r7],RL4                 
027b74: 0872           add r7,#0x2                   
027b76: 0865           add r6,#0x5                   
027b78: a980           movb RL4,[r0]                 
027b7a: 0981           addb RL4,#0x1                 
027b7c: b980           movb [r0],RL4                 
027b7e: a980           movb RL4,[r0]                 
027b80: 43f8a4f1       cmpb RL4,0xf1a4               
027b84: 8dbb           jmpr cc_C,0x027afc             -> 027afc
027b86: 8a6f1170       jb 0xfdde.0x7,0x027bac         -> 027bac
027b8a: e7fa1200       movb RL5,#0x12                
027b8e: f7fa20e5       movb 0xe520,RL5               
027b92: e6f420e5       mov r4,#0xe520                
027b96: f057           mov r5,r7                     
027b98: 2054           sub r5,r4                     
027b9a: 0851           add r5,#0x1                   
027b9c: f7fa21e5       movb 0xe521,RL5               
027ba0: e7f8a000       movb RL4,#0xa0                
027ba4: f7f822e5       movb 0xe522,RL4               
027ba8: ea00a63f       jmpa cc_UC,0x023fa6            -> 023fa6
027bac: 7e6f           bclr 0xfdde.0x7               
027bae: da024e38       calls 0x02384e                 -> 02384e
027bb2: ea00a63f       jmpa cc_UC,0x023fa6            -> 023fa6
027d20: f3f805e9       movb RL4,0xe905               
027d24: f7f830e5       movb 0xe530,RL4               
027d28: f3f804e9       movb RL4,0xe904               
027d2c: f7f831e5       movb 0xe531,RL4               
027d30: f3f89cfc       movb RL4,0xfc9c               
027d34: f7f832e5       movb 0xe532,RL4               
027d38: f2f49cfa       mov r4,0xfa9c                 
027d3c: 66f4ff03       and r4,#0x3ff                 
027d40: 7c24           shr r4,#0x2                   
027d42: f7f833e5       movb 0xe533,RL4               
027d46: f3f81ff0       movb RL4,0xf01f               
027d4a: f7f834e5       movb 0xe534,RL4               
027d4e: f3f81ef0       movb RL4,0xf01e               
027d52: f7f835e5       movb 0xe535,RL4               
027d56: f3f8cbf0       movb RL4,0xf0cb               
027d5a: f7f836e5       movb 0xe536,RL4               
027d5e: f3f8caf0       movb RL4,0xf0ca               
027d62: f7f837e5       movb 0xe537,RL4               
027d66: f3f889f1       movb RL4,0xf189               
027d6a: f7f838e5       movb 0xe538,RL4               
027d6e: f3f891f1       movb RL4,0xf191               
027d72: f7f839e5       movb 0xe539,RL4               
027d76: f3f864f0       movb RL4,0xf064               
027d7a: f7f83ae5       movb 0xe53a,RL4               
027d7e: f3f810f1       movb RL4,0xf110               
027d82: f7f83be5       movb 0xe53b,RL4               
027d86: ea00a63f       jmpa cc_UC,0x023fa6            -> 023fa6
0286f8: 9a0804e0       jnb 0xfd10.0xe,0x028704        -> 028704
0286fc: f3fc1c03       movb RL6,0x31c                
028700: ee08           bclr 0xfd10.0xe               
028702: 0d10           jmpr cc_UC,0x028724            -> 028724
028704: 9a1d0720       jnb 0xfd3a.0x2,0x028716        -> 028716
028708: f3f862ef       movb RL4,0xef62               
02870c: 4980           cmpb RL4,#0x0                 
02870e: fd03           jmpr cc_ULE,0x028716           -> 028716
028710: f3fc1e03       movb RL6,0x31e                
028714: 0d07           jmpr cc_UC,0x028724            -> 028724
028716: 9a0a0320       jnb 0xfd14.0x2,0x028720        -> 028720
02871a: f3fc1d03       movb RL6,0x31d                
02871e: 0d02           jmpr cc_UC,0x028724            -> 028724
028720: f3fc1b03       movb RL6,0x31b                
028724: f7fc78f1       movb 0xf178,RL6               
028728: f3f8e5e8       movb RL4,0xe8e5               
02872c: 43f8a502       cmpb RL4,0x2a5                
028730: 9d14           jmpr cc_NC,0x02875a            -> 02875a
028732: f2f43afc       mov r4,0xfc3a                 
028736: 42f40801       cmp r4,0x108                  
02873a: 9d0f           jmpr cc_NC,0x02875a            -> 02875a
02873c: f0c8           mov r12,r8                    
02873e: c0cd           movbz r13,RL6                 
028740: da0280c8       calls 0x02c880                 -> 02c880
028744: f094           mov r9,r4                     
028746: 0d34           jmpr cc_UC,0x0287b0            -> 0287b0
028756: 34000d47       subc 0x470d,0xfe00            
02875a: f0c8           mov r12,r8                    
02875c: d4d81e00       mov r13,[r8+#0x1e]            
028760: c0ce           movbz r14,RL6                 
028762: da02f0c7       calls 0x02c7f0                 -> 02c7f0
028766: f094           mov r9,r4                     
028768: 0d23           jmpr cc_UC,0x0287b0            -> 0287b0
0287b0: d4c80600       mov r12,[r8+#0x6]             
0287b4: f0d9           mov r13,r9                    
0287b6: da02debe       calls 0x02bede                 -> 02bede
0287ba: c4480600       mov [r8+#0x6],r4              
0287be: d4c80800       mov r12,[r8+#0x8]             
0287c2: f0d9           mov r13,r9                    
0287c4: da02debe       calls 0x02bede                 -> 02bede
0287c8: c4480800       mov [r8+#0x8],r4              
0287cc: d4c82000       mov r12,[r8+#0x20]            
0287d0: f0d9           mov r13,r9                    
0287d2: da02debe       calls 0x02bede                 -> 02bede
0287d6: c4482000       mov [r8+#0x20],r4             
0287da: d4c81e00       mov r12,[r8+#0x1e]            
0287de: f0d9           mov r13,r9                    
0287e0: da02debe       calls 0x02bede                 -> 02bede
0287e4: c4481e00       mov [r8+#0x1e],r4             
0287e8: 9890           mov r9,[r0+]                  
0287ea: 9880           mov r8,[r0+]                  
0287ec: 9860           mov r6,[r0+]                  
0287ee: db00           rets                          
02881e: f094           mov r9,r4                     
028820: d4c61800       mov r12,[r6+#0x18]            
028824: f0d9           mov r13,r9                    
028826: da03a444       calls 0x0344a4                 -> 0344a4
02882a: f084           mov r8,r4                     
02882c: 42f84c01       cmp r8,0x14c                  
028830: 9d02           jmpr cc_NC,0x028836            -> 028836
028832: f2f84c01       mov r8,0x14c                  
028836: 06f90080       add r9,#0x8000                
02883a: 0d19           jmpr cc_UC,0x02886e            -> 02886e
02886e: c4861800       mov [r6+#0x18],r8             
028872: f049           mov r4,r9                     
028874: 9890           mov r9,[r0+]                  
028876: 9880           mov r8,[r0+]                  
028878: 9870           mov r7,[r0+]                  
02887a: 9860           mov r6,[r0+]                  
02887c: 0802           add r0,#0x2                   
02887e: db00           rets                          
028896: 1000           addc r0,r0                    
028898: da02ccc8       calls 0x02c8cc                 -> 02c8cc
02889c: c4471000       mov [r7+#0x10],r4             
0288a0: f0c7           mov r12,r7                    
0288a2: f2fd3afc       mov r13,0xfc3a                
0288a6: da0208be       calls 0x02be08                 -> 02be08
0288aa: f0c7           mov r12,r7                    
0288ac: da0268c9       calls 0x02c968                 -> 02c968
0288b0: f084           mov r8,r4                     
0288b2: d4971400       mov r9,[r7+#0x14]             
0288b6: 2098           sub r9,r8                     
0288b8: 06f90080       add r9,#0x8000                
0288bc: c4871400       mov [r7+#0x14],r8             
0288c0: f049           mov r4,r9                     
0288c2: 9890           mov r9,[r0+]                  
0288c4: 9880           mov r8,[r0+]                  
0288c6: 9870           mov r7,[r0+]                  
0288c8: 0802           add r0,#0x2                   
0288ca: db00           rets                          
028972: 46f90050       cmp r9,#0x5000                
028976: ed0c           jmpr cc_UGT,0x028990           -> 028990
028978: f0c9           mov r12,r9                    
02897a: f2fd78ef       mov r13,0xef78                
02897e: da034447       calls 0x034744                 -> 034744
028982: f094           mov r9,r4                     
028984: 46f90050       cmp r9,#0x5000                
028988: fd15           jmpr cc_ULE,0x0289b4           -> 0289b4
02898a: e6f90050       mov r9,#0x5000                
02898e: 0d12           jmpr cc_UC,0x0289b4            -> 0289b4
028990: f049           mov r4,r9                     
028992: 8140           neg r4                        
028994: f094           mov r9,r4                     
028996: f0c9           mov r12,r9                    
028998: f2fd78ef       mov r13,0xef78                
02899c: da034447       calls 0x034744                 -> 034744
0289a0: f094           mov r9,r4                     
0289a2: 46f90050       cmp r9,#0x5000                
0289a6: ed04           jmpr cc_UGT,0x0289b0           -> 0289b0
0289a8: f049           mov r4,r9                     
0289aa: 8140           neg r4                        
0289ac: f094           mov r9,r4                     
0289ae: 0d02           jmpr cc_UC,0x0289b4            -> 0289b4
0289b0: e6f900b0       mov r9,#0xb000                
0289b4: f049           mov r4,r9                     
0289b6: 9890           mov r9,[r0+]                  
0289b8: 0802           add r0,#0x2                   
0289ba: db00           rets                          
0289d4: 4a072502       bmov 0xfd4a.0x2,0xfd0e.0x0    
0289d8: 4a072527       bmov 0xfd4a.0x7,0xfd0e.0x2    
0289dc: 48c1           cmp r12,#0x1                  
0289de: 3d47           jmpr cc_NE,0x028a6e            -> 028a6e
0289e0: f2f4caf0       mov r4,0xf0ca                 
0289e4: 7c84           shr r4,#0x8                   
0289e6: f7f89ada       movb 0xda9a,RL4               
0289ea: f2f4d4f0       mov r4,0xf0d4                 
0289ee: 06f40080       add r4,#0x8000                
0289f2: f6f492da       mov 0xda92,r4                 
0289f6: f2f5d6f0       mov r5,0xf0d6                 
0289fa: f6f590da       mov 0xda90,r5                 
0289fe: f2f2d8f0       mov r2,0xf0d8                 
028a02: 06f20080       add r2,#0x8000                
028a06: f6f294da       mov 0xda94,r2                 
028a0a: f2f3dcf0       mov r3,0xf0dc                 
028a0e: f6f39eda       mov 0xda9e,r3                 
028a12: f3f8e5f0       movb RL4,0xf0e5               
028a16: f7f8a1da       movb 0xdaa1,RL4               
028a1a: f3fae3f0       movb RL5,0xf0e3               
028a1e: f7fa98da       movb 0xda98,RL5               
028a22: f3f6e1f0       movb RL3,0xf0e1               
028a26: f7f6a0da       movb 0xdaa0,RL3               
028a2a: f3f4e9f0       movb RL2,0xf0e9               
028a2e: f7f499da       movb 0xda99,RL2               
028a32: f2f4e6f0       mov r4,0xf0e6                 
028a36: 7c54           shr r4,#0x5                   
028a38: f7f89cda       movb 0xda9c,RL4               
028a3c: f3f248fd       movb RL1,0xfd48               
028a40: f7f296da       movb 0xda96,RL1               
028a44: f3f84afd       movb RL4,0xfd4a               
028a48: f7f89bda       movb 0xda9b,RL4               
028a4c: f3f8eff0       movb RL4,0xf0ef               
028a50: f7f8a9da       movb 0xdaa9,RL4               
028a54: f3f8edf0       movb RL4,0xf0ed               
028a58: f7f8abda       movb 0xdaab,RL4               
028a5c: f3f8f4f0       movb RL4,0xf0f4               
028a60: f7f8afda       movb 0xdaaf,RL4               
028a64: f3f8ebf0       movb RL4,0xf0eb               
028a68: f7f8adda       movb 0xdaad,RL4               
028a6c: db00           rets                          
028a6e: f2f41ef0       mov r4,0xf01e                 
028a72: 7c84           shr r4,#0x8                   
028a74: f7f880da       movb 0xda80,RL4               
028a78: f2f428f0       mov r4,0xf028                 
028a7c: 06f40080       add r4,#0x8000                
028a80: f6f430da       mov 0xda30,r4                 
028a84: f2f52af0       mov r5,0xf02a                 
028a88: f6f52cda       mov 0xda2c,r5                 
028a8c: f2f22cf0       mov r2,0xf02c                 
028a90: 06f20080       add r2,#0x8000                
028a94: f6f22eda       mov 0xda2e,r2                 
028a98: f2f330f0       mov r3,0xf030                 
028a9c: f6f386da       mov 0xda86,r3                 
028aa0: f3f839f0       movb RL4,0xf039               
028aa4: f7f877da       movb 0xda77,RL4               
028aa8: f3fa37f0       movb RL5,0xf037               
028aac: f7fa79da       movb 0xda79,RL5               
028ab0: f2f474f1       mov r4,0xf174                 
028ab4: 7c84           shr r4,#0x8                   
028ab6: f7f87ada       movb 0xda7a,RL4               
028aba: f3f635f0       movb RL3,0xf035               
028abe: f7f676da       movb 0xda76,RL3               
028ac2: f3f43df0       movb RL2,0xf03d               
028ac6: f7f47dda       movb 0xda7d,RL2               
028aca: f2f43af0       mov r4,0xf03a                 
028ace: 7c54           shr r4,#0x5                   
028ad0: f7f848da       movb 0xda48,RL4               
028ad4: f3f248fd       movb RL1,0xfd48               
028ad8: f7f259da       movb 0xda59,RL1               
028adc: f3f84afd       movb RL4,0xfd4a               
028ae0: f7f881da       movb 0xda81,RL4               
028ae4: f3f843f0       movb RL4,0xf043               
028ae8: f7f8a8da       movb 0xdaa8,RL4               
028aec: f3f841f0       movb RL4,0xf041               
028af0: f7f8aada       movb 0xdaaa,RL4               
028af4: f3f848f0       movb RL4,0xf048               
028af8: f7f8aeda       movb 0xdaae,RL4               
028afc: f3f83ff0       movb RL4,0xf03f               
028b00: f7f8acda       movb 0xdaac,RL4               
028b04: db00           rets                          
029080: 9d06           jmpr cc_NC,0x02908e            -> 02908e
029082: 42f75ce9       cmp r7,0xe95c                 
029086: fd03           jmpr cc_ULE,0x02908e           -> 02908e
029088: f6f75ce9       mov 0xe95c,r7                 
02908c: 0d16           jmpr cc_UC,0x0290ba            -> 0290ba
02908e: f6f95ce9       mov 0xe95c,r9                 
029092: 0d13           jmpr cc_UC,0x0290ba            -> 0290ba
0290ba: f2f45ee9       mov r4,0xe95e                 
0290be: 46f4ffff       cmp r4,#0xffff                
0290c2: 9d18           jmpr cc_NC,0x0290f4            -> 0290f4
0290c4: e6fc141d       mov r12,#0x1d14               
0290c8: c2fd3cfc       movbz r13,0xfc3c              
0290cc: da03ac48       calls 0x0348ac                 -> 0348ac
0290d0: f3f84be9       movb RL4,0xe94b               
0290d4: 2984           subb RL4,#0x4                 
0290d6: c084           movbz r4,RL4                  
0290d8: 8840           mov [-r0],r4                  
0290da: e6fc161d       mov r12,#0x1d16               
0290de: da03344a       calls 0x034a34                 -> 034a34
0290e2: c08d           movbz r13,RL4                 
0290e4: 9840           mov r4,[r0+]                  
0290e6: 4cd4           shl r13,r4                    
0290e8: f2fc5ee9       mov r12,0xe95e                
0290ec: da038444       calls 0x034484                 -> 034484
0290f0: f6f45ee9       mov 0xe95e,r4                 
0290f4: e6fc5c1e       mov r12,#0x1e5c               
0290f8: c2fd66ef       movbz r13,0xef66              
0290fc: da03fc48       calls 0x0348fc                 -> 0348fc
029100: 9a0a0720       jnb 0xfd14.0x2,0x029112        -> 029112
029104: e6fc5e1e       mov r12,#0x1e5e               
029108: f2fd2ae9       mov r13,0xe92a                
02910c: da035049       calls 0x034950                 -> 034950
029110: 0d06           jmpr cc_UC,0x02911e            -> 02911e
029112: e6fc5e1e       mov r12,#0x1e5e               
029116: f2fd3afc       mov r13,0xfc3a                
02911a: da035049       calls 0x034950                 -> 034950
02911e: e6fc601e       mov r12,#0x1e60               
029122: da036a4a       calls 0x034a6a                 -> 034a6a
029126: c084           movbz r4,RL4                  
029128: 5c84           shl r4,#0x8                   
02912a: 8840           mov [-r0],r4                  
02912c: f2fc5ce9       mov r12,0xe95c                
029130: f2fd54e9       mov r13,0xe954                
029134: da032e45       calls 0x03452e                 -> 03452e
029138: f0c4           mov r12,r4                    
02913a: 98d0           mov r13,[r0+]                 
02913c: da03dc46       calls 0x0346dc                 -> 0346dc
029140: f0c4           mov r12,r4                    
029142: f2fd5ee9       mov r13,0xe95e                
029146: da03ce46       calls 0x0346ce                 -> 0346ce
02914a: f0d4           mov r13,r4                    
02914c: f2fcf8e8       mov r12,0xe8f8                
029150: da038444       calls 0x034484                 -> 034484
029154: f6f4f8e8       mov 0xe8f8,r4                 
029158: 8a0c0ba0       jb 0xfd18.0xa,0x029172         -> 029172
02915c: e6fc581e       mov r12,#0x1e58               
029160: da03344a       calls 0x034a34                 -> 034a34
029164: c084           movbz r4,RL4                  
029166: 5c84           shl r4,#0x8                   
029168: 06f40080       add r4,#0x8000                
02916c: f6f418e9       mov 0xe918,r4                 
029170: 0d24           jmpr cc_UC,0x0291ba            -> 0291ba
029172: f2f418e9       mov r4,0xe918                 
029176: 2d21           jmpr cc_EQ,0x0291ba            -> 0291ba
029178: 4840           cmp r4,#0x0                   
02917a: bd10           jmpr cc_SLE,0x02919c           -> 02919c
02917c: c2f54be9       movbz r5,0xe94b               
029180: 2852           sub r5,#0x2                   
029182: c2f2f502       movbz r2,0x2f5                
029186: 4c25           shl r2,r5                     
029188: f084           mov r8,r4                     
02918a: 2082           sub r8,r2                     
02918c: 4880           cmp r8,#0x0                   
02918e: bd03           jmpr cc_SLE,0x029196           -> 029196
029190: f6f818e9       mov 0xe918,r8                 
029194: 0d12           jmpr cc_UC,0x0291ba            -> 0291ba
029196: f68e18e9       mov 0xe918,0xff1c             
02919a: 0d0f           jmpr cc_UC,0x0291ba            -> 0291ba
02919c: c2f44be9       movbz r4,0xe94b               
0291a0: 2842           sub r4,#0x2                   
0291a2: c2f8f502       movbz r8,0x2f5                
0291a6: 4c84           shl r8,r4                     
0291a8: 02f818e9       add r8,0xe918                 
0291ac: 4880           cmp r8,#0x0                   
0291ae: dd03           jmpr cc_SGE,0x0291b6           -> 0291b6
0291b0: f6f818e9       mov 0xe918,r8                 
0291b4: 0d02           jmpr cc_UC,0x0291ba            -> 0291ba
0291b6: f68e18e9       mov 0xe918,0xff1c             
0291ba: f2fcf8e8       mov r12,0xe8f8                
0291be: f2fd18e9       mov r13,0xe918                
0291c2: da032e45       calls 0x03452e                 -> 03452e
0291c6: f6f4f8e8       mov 0xe8f8,r4                 
0291ca: 9a145990       jnb 0xfd28.0x9,0x029280        -> 029280
0291ce: 9a0a0920       jnb 0xfd14.0x2,0x0291e4        -> 0291e4
0291d2: e6fc8c30       mov r12,#0x308c               
0291d6: f2f42ae9       mov r4,0xe92a                 
0291da: 7c54           shr r4,#0x5                   
0291dc: c08d           movbz r13,RL4                 
0291de: da03ac48       calls 0x0348ac                 -> 0348ac
0291e2: 0d06           jmpr cc_UC,0x0291f0            -> 0291f0
0291e4: e6fc8c30       mov r12,#0x308c               
0291e8: c2fd3cfc       movbz r13,0xfc3c              
0291ec: da03ac48       calls 0x0348ac                 -> 0348ac
0291f0: e6fc8e30       mov r12,#0x308e               
0291f4: da03344a       calls 0x034a34                 -> 034a34
0291f8: c08d           movbz r13,RL4                 
0291fa: 5c8d           shl r13,#0x8                  
0291fc: f2fcf8e8       mov r12,0xe8f8                
029200: da038444       calls 0x034484                 -> 034484
029204: f6f4f8e8       mov 0xe8f8,r4                 
029208: 9a140e90       jnb 0xfd28.0x9,0x029228        -> 029228
02920c: 8a140ca0       jb 0xfd28.0xa,0x029228         -> 029228
029210: e6fc8830       mov r12,#0x3088               
029214: da03344a       calls 0x034a34                 -> 034a34
029218: c084           movbz r4,RL4                  
02921a: 5c84           shl r4,#0x8                   
02921c: 06f40080       add r4,#0x8000                
029220: f6f41ae9       mov 0xe91a,r4                 
029224: af14           bset 0xfd28.0xa               
029226: 0d24           jmpr cc_UC,0x029270            -> 029270
029228: f2f41ae9       mov r4,0xe91a                 
02922c: 2d21           jmpr cc_EQ,0x029270            -> 029270
02922e: 4840           cmp r4,#0x0                   
029230: bd10           jmpr cc_SLE,0x029252           -> 029252
029232: c2f54be9       movbz r5,0xe94b               
029236: 2852           sub r5,#0x2                   
029238: c2f2f802       movbz r2,0x2f8                
02923c: 4c25           shl r2,r5                     
02923e: f084           mov r8,r4                     
029240: 2082           sub r8,r2                     
029242: 4880           cmp r8,#0x0                   
029244: bd03           jmpr cc_SLE,0x02924c           -> 02924c
029246: f6f81ae9       mov 0xe91a,r8                 
02924a: 0d12           jmpr cc_UC,0x029270            -> 029270
02924c: f68e1ae9       mov 0xe91a,0xff1c             
029250: 0d0f           jmpr cc_UC,0x029270            -> 029270
029252: c2f44be9       movbz r4,0xe94b               
029256: 2842           sub r4,#0x2                   
029258: c2f8f802       movbz r8,0x2f8                
02925c: 4c84           shl r8,r4                     
02925e: 02f81ae9       add r8,0xe91a                 
029262: 4880           cmp r8,#0x0                   
029264: dd03           jmpr cc_SGE,0x02926c           -> 02926c
029266: f6f81ae9       mov 0xe91a,r8                 
02926a: 0d02           jmpr cc_UC,0x029270            -> 029270
02926c: f68e1ae9       mov 0xe91a,0xff1c             
029270: f2fcf8e8       mov r12,0xe8f8                
029274: f2fd1ae9       mov r13,0xe91a                
029278: da032e45       calls 0x03452e                 -> 03452e
02927c: f6f4f8e8       mov 0xe8f8,r4                 
029280: f3f82ce9       movb RL4,0xe92c               
029284: 4980           cmpb RL4,#0x0                 
029286: fd21           jmpr cc_ULE,0x0292ca           -> 0292ca
029288: e6fc961e       mov r12,#0x1e96               
02928c: c08d           movbz r13,RL4                 
02928e: da03ac48       calls 0x0348ac                 -> 0348ac
029292: e6fc981e       mov r12,#0x1e98               
029296: da03344a       calls 0x034a34                 -> 034a34
02929a: c089           movbz r9,RL4                  
02929c: 5c89           shl r9,#0x8                   
02929e: e6fcb41d       mov r12,#0x1db4               
0292a2: c2fd94f6       movbz r13,0xf694              
0292a6: da03ac48       calls 0x0348ac                 -> 0348ac
0292aa: e6fcb61d       mov r12,#0x1db6               
0292ae: da03344a       calls 0x034a34                 -> 034a34
0292b2: c08d           movbz r13,RL4                 
0292b4: 5c8d           shl r13,#0x8                  
0292b6: f0c9           mov r12,r9                    
0292b8: da03ce46       calls 0x0346ce                 -> 0346ce
0292bc: f0d4           mov r13,r4                    
0292be: f2fcf8e8       mov r12,0xe8f8                
0292c2: da038444       calls 0x034484                 -> 034484
0292c6: f6f4f8e8       mov 0xe8f8,r4                 
0292ca: f2f462e9       mov r4,0xe962                 
0292ce: 4840           cmp r4,#0x0                   
0292d0: fd18           jmpr cc_ULE,0x029302           -> 029302
0292d2: e6fcfe0a       mov r12,#0xafe                
0292d6: c2fd3cfc       movbz r13,0xfc3c              
0292da: da03ac48       calls 0x0348ac                 -> 0348ac
0292de: e6fcfc0a       mov r12,#0xafc                
0292e2: f2fd62e9       mov r13,0xe962                
0292e6: da039e49       calls 0x03499e                 -> 03499e
0292ea: e6fc000b       mov r12,#0xb00                
0292ee: da036a4a       calls 0x034a6a                 -> 034a6a
0292f2: c08d           movbz r13,RL4                 
0292f4: 5c8d           shl r13,#0x8                  
0292f6: f2fcf8e8       mov r12,0xe8f8                
0292fa: da038444       calls 0x034484                 -> 034484
0292fe: f6f4f8e8       mov 0xe8f8,r4                 
029302: e6fcde1d       mov r12,#0x1dde               
029306: c2fd94f6       movbz r13,0xf694              
02930a: da03ac48       calls 0x0348ac                 -> 0348ac
02930e: 9a0a1f20       jnb 0xfd14.0x2,0x029350        -> 029350
029312: f2f4b0f4       mov r4,0xf4b0                 
029316: 4840           cmp r4,#0x0                   
029318: fd1b           jmpr cc_ULE,0x029350           -> 029350
02931a: e6fcdc1d       mov r12,#0x1ddc               
02931e: f0d4           mov r13,r4                    
029320: da039e49       calls 0x03499e                 -> 03499e
029324: 9a0c0760       jnb 0xfd18.0x6,0x029336        -> 029336
029328: e6fcde1e       mov r12,#0x1ede               
02932c: da036a4a       calls 0x034a6a                 -> 034a6a
029330: c089           movbz r9,RL4                  
029332: 5c89           shl r9,#0x8                   
029334: 0d06           jmpr cc_UC,0x029342            -> 029342
029336: e6fce01d       mov r12,#0x1de0               
02933a: da036a4a       calls 0x034a6a                 -> 034a6a
02933e: c089           movbz r9,RL4                  
029340: 5c89           shl r9,#0x8                   
029342: f2fcf8e8       mov r12,0xe8f8                
029346: f0d9           mov r13,r9                    
029348: da038444       calls 0x034484                 -> 034484
02934c: f6f4f8e8       mov 0xe8f8,r4                 
029350: f3f8a5f4       movb RL4,0xf4a5               
029354: 47f88000       cmpb RL4,#0x80                
029358: 2d11           jmpr cc_EQ,0x02937c            -> 02937c
02935a: e6fc1a1e       mov r12,#0x1e1a               
02935e: c08d           movbz r13,RL4                 
029360: da03fc48       calls 0x0348fc                 -> 0348fc
029364: e6fc1e1e       mov r12,#0x1e1e               
029368: da036a4a       calls 0x034a6a                 -> 034a6a
02936c: c08d           movbz r13,RL4                 
02936e: 5c8d           shl r13,#0x8                  
029370: f2fcf8e8       mov r12,0xe8f8                
029374: da038444       calls 0x034484                 -> 034484
029378: f6f4f8e8       mov 0xe8f8,r4                 
02937c: 9a0a1420       jnb 0xfd14.0x2,0x0293a8        -> 0293a8
029380: 9a171200       jnb 0xfd2e.0x0,0x0293a8        -> 0293a8
029384: e6fc121e       mov r12,#0x1e12               
029388: da03344a       calls 0x034a34                 -> 034a34
02938c: c08d           movbz r13,RL4                 
02938e: 5c8d           shl r13,#0x8                  
029390: c2fce0e9       movbz r12,0xe9e0              
029394: 5c8c           shl r12,#0x8                  
029396: da03ce46       calls 0x0346ce                 -> 0346ce
02939a: f0d4           mov r13,r4                    
02939c: f2fcf8e8       mov r12,0xe8f8                
0293a0: da038444       calls 0x034484                 -> 034484
0293a4: f6f4f8e8       mov 0xe8f8,r4                 
0293a8: 9890           mov r9,[r0+]                  
0293aa: 9880           mov r8,[r0+]                  
0293ac: 9870           mov r7,[r0+]                  
0293ae: 9860           mov r6,[r0+]                  
0293b0: db00           rets                          
0299b4: 4ce9           shl r14,r9                    
0299b6: e7f88000       movb RL4,#0x80                
0299ba: f7f8ffe8       movb 0xe8ff,RL4               
0299be: f68e14e9       mov 0xe914,0xff1c             
0299c2: f68e16e9       mov 0xe916,0xff1c             
0299c6: f68e18e9       mov 0xe918,0xff1c             
0299ca: f2f420e9       mov r4,0xe920                 
0299ce: 5c24           shl r4,#0x2                   
0299d0: f6f42ee9       mov 0xe92e,r4                 
0299d4: f68e30e9       mov 0xe930,0xff1c             
0299d8: f68e32e9       mov 0xe932,0xff1c             
0299dc: f68e34e9       mov 0xe934,0xff1c             
0299e0: f68e36e9       mov 0xe936,0xff1c             
0299e4: f68e3ce9       mov 0xe93c,0xff1c             
0299e8: f68e3ae9       mov 0xe93a,0xff1c             
0299ec: f68e40e9       mov 0xe940,0xff1c             
0299f0: f68e42e9       mov 0xe942,0xff1c             
0299f4: f68e44e9       mov 0xe944,0xff1c             
0299f8: f68e26fd       mov 0xfd26,0xff1c             
0299fc: 4a0c0c68       bmov 0xfd18.0x8,0xfd18.0x6    
029a00: 4a0c0c49       bmov 0xfd18.0x9,0xfd18.0x4    
029a04: 4a0c0cba       bmov 0xfd18.0xa,0xfd18.0xb    
029a08: c2f95ae9       movbz r9,0xe95a               
029a0c: c2f55be9       movbz r5,0xe95b               
029a10: 1b95           mulu r9,r5                    
029a12: f2f90efe       mov r9,0xfe0e                 
029a16: 7c79           shr r9,#0x7                   
029a18: 46f9ff00       cmp r9,#0xff                  
029a1c: fd03           jmpr cc_ULE,0x029a24           -> 029a24
029a1e: f68f06e9       mov 0xe906,0xff1e             
029a22: 0d05           jmpr cc_UC,0x029a2e            -> 029a2e
029a24: f049           mov r4,r9                     
029a26: c084           movbz r4,RL4                  
029a28: 5c84           shl r4,#0x8                   
029a2a: f6f406e9       mov 0xe906,r4                 
029a2e: f3f807e9       movb RL4,0xe907               
029a32: f7f841da       movb 0xda41,RL4               
029a36: 9a1a0600       jnb 0xfd34.0x0,0x029a46        -> 029a46
029a3a: f3fa9aeb       movb RL5,0xeb9a               
029a3e: 67fa0f00       andb RL5,#0xf                 
029a42: f7faeee8       movb 0xe8ee,RL5               
029a46: 9a1a0e10       jnb 0xfd34.0x1,0x029a66        -> 029a66
029a4a: f3f8a6eb       movb RL4,0xeba6               
029a4e: 67f80f00       andb RL4,#0xf                 
029a52: f7f8efe8       movb 0xe8ef,RL4               
029a56: c2f4b2eb       movbz r4,0xebb2               
029a5a: 66f44100       and r4,#0x41                  
029a5e: 46f44100       cmp r4,#0x41                  
029a62: 3d01           jmpr cc_NE,0x029a66            -> 029a66
029a64: af22           bset 0xfd44.0xa               
029a66: da0298cd       calls 0x02cd98                 -> 02cd98
029a6a: f2fc04e9       mov r12,0xe904                
029a6e: da02848a       calls 0x028a84                 -> 028a84
029a72: 9890           mov r9,[r0+]                  
029a74: db00           rets                          
02a3ba: 0d04           jmpr cc_UC,0x02a3c4            -> 02a3c4
02a3c4: f2f42ee9       mov r4,0xe92e                 
02a3c8: 7c24           shr r4,#0x2                   
02a3ca: f6f42ae9       mov 0xe92a,r4                 
02a3ce: f0c4           mov r12,r4                    
02a3d0: f0d9           mov r13,r9                    
02a3d2: da03a444       calls 0x0344a4                 -> 0344a4
02a3d6: f6f462e9       mov 0xe962,r4                 
02a3da: 9890           mov r9,[r0+]                  
02a3dc: 9880           mov r8,[r0+]                  
02a3de: 9870           mov r7,[r0+]                  
02a3e0: 9860           mov r6,[r0+]                  
02a3e2: db00           rets                          
02ac00: 43fafd33       cmpb RL5,0x33fd               
02ac04: 8d02           jmpr cc_C,0x02ac0a             -> 02ac0a
02ac06: 7f11           bset 0xfd22.0x7               
02ac08: 5188           xorb RL4,RL4                  
02ac0a: 47f8f600       cmpb RL4,#0xf6                
02ac0e: 8d02           jmpr cc_C,0x02ac14             -> 02ac14
02ac10: e7f8f600       movb RL4,#0xf6                
02ac14: f7f8e6f6       movb 0xf6e6,RL4               
02ac18: f7f856da       movb 0xda56,RL4               
02ac1c: db00           rets                          
02aca8: 8000           cmpi1 r0,#0x0                 
02acaa: d08c           movbs r12,RL4                 
02acac: c2fd08e8       movbz r13,0xe808              
02acb0: da033e46       calls 0x03463e                 -> 03463e
02acb4: f7f808e8       movb 0xe808,RL4               
02acb8: 05f86ee9       addb 0xe96e,RL4               
02acbc: db00           rets                          
02ad3c: da036a4a       calls 0x034a6a                 -> 034a6a
02ad40: e4800300       movb [r0+#0x3],RL4            
02ad44: e11c           movb RL6,#0x1                 
02ad46: ea006eef       jmpa cc_UC,0x02ef6e            -> 02ef6e
02ada4: 2d2e           jmpr cc_EQ,0x02ae02            -> 02ae02
02ada6: e6fc2e22       mov r12,#0x222e               
02adaa: f2fd54fc       mov r13,0xfc54                
02adae: da035049       calls 0x034950                 -> 034950
02adb2: a848           mov r4,[r8]                   
02adb4: d4500200       mov r5,[r0+#0x2]              
02adb8: 4054           cmp r5,r4                     
02adba: fd11           jmpr cc_ULE,0x02adde           -> 02adde
02adbc: 8850           mov [-r0],r5                  
02adbe: e6fc3022       mov r12,#0x2230               
02adc2: da03504a       calls 0x034a50                 -> 034a50
02adc6: f0d4           mov r13,r4                    
02adc8: a8c8           mov r12,[r8]                  
02adca: da038444       calls 0x034484                 -> 034484
02adce: b848           mov [r8],r4                   
02add0: 9850           mov r5,[r0+]                  
02add2: 4045           cmp r4,r5                     
02add4: 8d16           jmpr cc_C,0x02ae02             -> 02ae02
02add6: d4400200       mov r4,[r0+#0x2]              
02adda: b848           mov [r8],r4                   
02addc: 0d12           jmpr cc_UC,0x02ae02            -> 02ae02
02adde: d4400200       mov r4,[r0+#0x2]              
02ade2: 8840           mov [-r0],r4                  
02ade4: e6fc2222       mov r12,#0x2222               
02ade8: da03504a       calls 0x034a50                 -> 034a50
02adec: f0d4           mov r13,r4                    
02adee: a8c8           mov r12,[r8]                  
02adf0: da03a444       calls 0x0344a4                 -> 0344a4
02adf4: b848           mov [r8],r4                   
02adf6: 9850           mov r5,[r0+]                  
02adf8: 4045           cmp r4,r5                     
02adfa: ed03           jmpr cc_UGT,0x02ae02           -> 02ae02
02adfc: d4400200       mov r4,[r0+#0x2]              
02ae00: b848           mov [r8],r4                   
02ae02: f4880100       movb RL4,[r8+#0x1]            
02ae06: 47f81900       cmpb RL4,#0x19                
02ae0a: 8d1e           jmpr cc_C,0x02ae48             -> 02ae48
02ae0c: 49c1           cmpb RL6,#0x1                 
02ae0e: 3d02           jmpr cc_NE,0x02ae14            -> 02ae14
02ae10: 9a1804b0       jnb 0xfd30.0xb,0x02ae1c        -> 02ae1c
02ae14: 49c2           cmpb RL6,#0x2                 
02ae16: 3d18           jmpr cc_NE,0x02ae48            -> 02ae48
02ae18: 8a1816c0       jb 0xfd30.0xc,0x02ae48         -> 02ae48
02ae1c: 8a0914a0       jb 0xfd12.0xa,0x02ae48         -> 02ae48
02ae20: 8a0a1250       jb 0xfd14.0x5,0x02ae48         -> 02ae48
02ae24: 8a221060       jb 0xfd44.0x6,0x02ae48         -> 02ae48
02ae28: f3f834fc       movb RL4,0xfc34               
02ae2c: 3d0d           jmpr cc_NE,0x02ae48            -> 02ae48
02ae2e: f3fa35fc       movb RL5,0xfc35               
02ae32: 3d0a           jmpr cc_NE,0x02ae48            -> 02ae48
02ae34: f4673700       movb RL3,[r7+#0x37]           
02ae38: 3d07           jmpr cc_NE,0x02ae48            -> 02ae48
02ae3a: f0c8           mov r12,r8                    
02ae3c: f0d7           mov r13,r7                    
02ae3e: c0ce           movbz r14,RL6                 
02ae40: da02b2f5       calls 0x02f5b2                 -> 02f5b2
02ae44: f094           mov r9,r4                     
02ae46: 0d04           jmpr cc_UC,0x02ae50            -> 02ae50
02ae48: f0c8           mov r12,r8                    
02ae4a: da026cf6       calls 0x02f66c                 -> 02f66c
02ae4e: f094           mov r9,r4                     
02ae50: 9a232020       jnb 0xfd46.0x2,0x02ae94        -> 02ae94
02ae54: 9a0a1750       jnb 0xfd14.0x5,0x02ae86        -> 02ae86
02ae58: f4880700       movb RL4,[r8+#0x7]            
02ae5c: b980           movb [r0],RL4                 
02ae5e: e6fc3c22       mov r12,#0x223c               
02ae62: c2fd3cfc       movbz r13,0xfc3c              
02ae66: da03ac48       calls 0x0348ac                 -> 0348ac
02ae6a: e6fc3e22       mov r12,#0x223e               
02ae6e: da03344a       calls 0x034a34                 -> 034a34
02ae72: e4800400       movb [r0+#0x4],RL4            
02ae76: f0c0           mov r12,r0                    
02ae78: c2fdff02       movbz r13,0x2ff               
02ae7c: c08e           movbz r14,RL4                 
02ae7e: c08f           movbz r15,RL4                 
02ae80: da0264f5       calls 0x02f564                 -> 02f564
02ae84: 0d0f           jmpr cc_UC,0x02aea4            -> 02aea4
02ae86: a8c8           mov r12,[r8]                  
02ae88: f0d9           mov r13,r9                    
02ae8a: da037246       calls 0x034672                 -> 034672
02ae8e: 7c84           shr r4,#0x8                   
02ae90: b980           movb [r0],RL4                 
02ae92: 0d08           jmpr cc_UC,0x02aea4            -> 02aea4
02ae94: a8c8           mov r12,[r8]                  
02ae96: c2fdfd02       movbz r13,0x2fd               
02ae9a: 5c8d           shl r13,#0x8                  
02ae9c: da037246       calls 0x034672                 -> 034672
02aea0: 7c84           shr r4,#0x8                   
02aea2: b980           movb [r0],RL4                 
02aea4: a980           movb RL4,[r0]                 
02aea6: c08c           movbz r12,RL4                 
02aea8: f4a80600       movb RL5,[r8+#0x6]            
02aeac: d0ad           movbs r13,RL5                 
02aeae: da030e45       calls 0x03450e                 -> 03450e
02aeb2: b980           movb [r0],RL4                 
02aeb4: 47f8f900       cmpb RL4,#0xf9                
02aeb8: 9d12           jmpr cc_NC,0x02aede            -> 02aede
02aeba: 4986           cmpb RL4,#0x6                 
02aebc: 8d05           jmpr cc_C,0x02aec8             -> 02aec8
02aebe: 9a231910       jnb 0xfd46.0x1,0x02aef4        -> 02aef4
02aec2: c4980200       mov [r8+#0x2],r9              
02aec6: 0d16           jmpr cc_UC,0x02aef4            -> 02aef4
02aec8: e168           movb RL4,#0x6                 
02aeca: b980           movb [r0],RL4                 
02aecc: d4480200       mov r4,[r8+#0x2]              
02aed0: 4094           cmp r9,r4                     
02aed2: fd10           jmpr cc_ULE,0x02aef4           -> 02aef4
02aed4: 9a230e10       jnb 0xfd46.0x1,0x02aef4        -> 02aef4
02aed8: c4980200       mov [r8+#0x2],r9              
02aedc: 0d0b           jmpr cc_UC,0x02aef4            -> 02aef4
02aede: e7f8f900       movb RL4,#0xf9                
02aee2: b980           movb [r0],RL4                 
02aee4: d4480200       mov r4,[r8+#0x2]              
02aee8: 4094           cmp r9,r4                     
02aeea: 9d04           jmpr cc_NC,0x02aef4            -> 02aef4
02aeec: 9a230210       jnb 0xfd46.0x1,0x02aef4        -> 02aef4
02aef0: c4980200       mov [r8+#0x2],r9              
02aef4: a980           movb RL4,[r0]                 
02aef6: e4880700       movb [r8+#0x7],RL4            
02aefa: 49c1           cmpb RL6,#0x1                 
02aefc: 3d1a           jmpr cc_NE,0x02af32            -> 02af32
02aefe: f3f89dfc       movb RL4,0xfc9d               
02af02: 43f87903       cmpb RL4,0x379                
02af06: ed09           jmpr cc_UGT,0x02af1a           -> 02af1a
02af08: f3fa89f1       movb RL5,0xf189               
02af0c: f7faecf6       movb 0xf6ec,RL5               
02af10: f3f689f1       movb RL3,0xf189               
02af14: f7f684da       movb 0xda84,RL3               
02af18: 0d06           jmpr cc_UC,0x02af26            -> 02af26
02af1a: e168           movb RL4,#0x6                 
02af1c: f7f8ecf6       movb 0xf6ec,RL4               
02af20: e16a           movb RL5,#0x6                 
02af22: f7fa84da       movb 0xda84,RL5               
02af26: f2f484f1       mov r4,0xf184                 
02af2a: 7c84           shr r4,#0x8                   
02af2c: f7f8b0da       movb 0xdab0,RL4               
02af30: 0d19           jmpr cc_UC,0x02af64            -> 02af64
02af32: f3f89dfc       movb RL4,0xfc9d               
02af36: 43f87903       cmpb RL4,0x379                
02af3a: ed09           jmpr cc_UGT,0x02af4e           -> 02af4e
02af3c: f3fa91f1       movb RL5,0xf191               
02af40: f7faedf6       movb 0xf6ed,RL5               
02af44: f3f691f1       movb RL3,0xf191               
02af48: f7f697da       movb 0xda97,RL3               
02af4c: 0d06           jmpr cc_UC,0x02af5a            -> 02af5a
02af4e: e168           movb RL4,#0x6                 
02af50: f7f8edf6       movb 0xf6ed,RL4               
02af54: e16a           movb RL5,#0x6                 
02af56: f7fa97da       movb 0xda97,RL5               
02af5a: f2f48cf1       mov r4,0xf18c                 
02af5e: 7c84           shr r4,#0x8                   
02af60: f7f8b1da       movb 0xdab1,RL4               
02af64: f2f40efd       mov r4,0xfd0e                 
02af68: c4470200       mov [r7+#0x2],r4              
02af6c: 09c1           addb RL6,#0x1                 
02af6e: 43fc76f1       cmpb RL6,0xf176               
02af72: eaf04aed       jmpa cc_ULE,0x02ed4a           -> 02ed4a
02af76: 0806           add r0,#0x6                   
02af78: 9890           mov r9,[r0+]                  
02af7a: 9880           mov r8,[r0+]                  
02af7c: 9870           mov r7,[r0+]                  
02af7e: 9860           mov r6,[r0+]                  
02af80: db00           rets                          
02be08: 5c24           shl r4,#0x2                   
02be0a: f0c4           mov r12,r4                    
02be0c: f04c           mov r4,r12                    
02be0e: 7c84           shr r4,#0x8                   
02be10: f7f849f7       movb 0xf749,RL4               
02be14: f05c           mov r5,r12                    
02be16: f7fa4af7       movb 0xf74a,RL5               
02be1a: e186           movb RL3,#0x8                 
02be1c: f7f650f7       movb 0xf750,RL3               
02be20: db00           rets                          
02bede: e7fa3300       movb RL5,#0x33                
02bee2: f7fa49f7       movb 0xf749,RL5               
02bee6: 0d03           jmpr cc_UC,0x02beee            -> 02beee
02beee: e178           movb RL4,#0x7                 
02bef0: f7f850f7       movb 0xf750,RL4               
02bef4: db00           rets                          
02c2ee: 1b61           mulu r6,r1                    
02c2f0: c2f470ec       movbz r4,0xec70               
02c2f4: 66f42000       and r4,#0x20                  
02c2f8: 2d04           jmpr cc_EQ,0x02c302            -> 02c302
02c2fa: e6f4fdff       mov r4,#0xfffd                
02c2fe: 64f4f8f5       and 0xf5f8,r4                 
02c302: e118           movb RL4,#0x1                 
02c304: f7f873ec       movb 0xec73,RL4               
02c308: e6fc72ec       mov r12,#0xec72               
02c30c: e6fdeca8       mov r13,#0xa8ec               
02c310: c2fe2102       movbz r14,0x221               
02c314: c2ff4802       movbz r15,0x248               
02c318: da02207d       calls 0x027d20                 -> 027d20
02c31c: 4a1c1b62       bmov 0xfd36.0x2,0xfd38.0x6    
02c320: c2f47cec       movbz r4,0xec7c               
02c324: 66f42000       and r4,#0x20                  
02c328: 2d04           jmpr cc_EQ,0x02c332            -> 02c332
02c32a: e6f4fbff       mov r4,#0xfffb                
02c32e: 64f4f8f5       and 0xf5f8,r4                 
02c332: ef03           bset 0xfd06.0xe               
02c334: cf03           bset 0xfd06.0xc               
02c336: e7f81400       movb RL4,#0x14                
02c33a: f7f814ea       movb 0xea14,RL4               
02c33e: f3fa0103       movb RL5,0x301                
02c342: f7fa90f6       movb 0xf690,RL5               
02c346: da035a58       calls 0x03585a                 -> 03585a
02c34a: da033a58       calls 0x03583a                 -> 03583a
02c34e: f7f894f6       movb 0xf694,RL4               
02c352: da033c59       calls 0x03593c                 -> 03593c
02c356: ff20           bset 0xfd40.0xf               
02c358: da037c5b       calls 0x035b7c                 -> 035b7c
02c35c: da037c5b       calls 0x035b7c                 -> 035b7c
02c360: da037c5b       calls 0x035b7c                 -> 035b7c
02c364: da037c5b       calls 0x035b7c                 -> 035b7c
02c368: da037c5b       calls 0x035b7c                 -> 035b7c
02c36c: da032abe       calls 0x03be2a                 -> 03be2a
02c370: da02baa3       calls 0x02a3ba                 -> 02a3ba
02c374: da036a9d       calls 0x039d6a                 -> 039d6a
02c378: da032850       calls 0x035028                 -> 035028
02c37c: da030000       calls 0x030000                 -> 030000
02c380: f2f466f5       mov r4,0xf566                 
02c384: 1c14           rol r4,#0x1                   
02c386: f6f466f5       mov 0xf566,r4                 
02c38a: 6841           and r4,#0x1                   
02c38c: 2d02           jmpr cc_EQ,0x02c392            -> 02c392
02c38e: f68f66f5       mov 0xf566,0xff1e             
02c392: f2f45e01       mov r4,0x15e                  
02c396: f6f46cf5       mov 0xf56c,r4                 
02c39a: f2f50c01       mov r5,0x10c                  
02c39e: f6f568f5       mov 0xf568,r5                 
02c3a2: e118           movb RL4,#0x1                 
02c3a4: f7f85df5       movb 0xf55d,RL4               
02c3a8: 9f29           bset 0xfd52.0x9               
02c3aa: e7fa1e00       movb RL5,#0x1e                
02c3ae: f7fa16ea       movb 0xea16,RL5               
02c3b2: da02222f       calls 0x022f22                 -> 022f22
02c3b6: e024           mov r4,#0x2                   
02c3b8: f6f496f7       mov 0xf796,r4                 
02c3bc: da023671       calls 0x027136                 -> 027136
02c3c0: da0298d9       calls 0x02d998                 -> 02d998
02c3c4: da035e63       calls 0x03635e                 -> 03635e
02c3c8: da02f470       calls 0x0270f4                 -> 0270f4
02c3cc: da02a4ad       calls 0x02ada4                 -> 02ada4
02c3d0: da02b499       calls 0x0299b4                 -> 0299b4
02c3d4: da03a814       calls 0x0314a8                 -> 0314a8
02c3d8: da036016       calls 0x031660                 -> 031660
02c3dc: da037a54       calls 0x03547a                 -> 03547a
02c3e0: 0e05           bclr 0xfd0a.0x0               
02c3e2: 9a0f0570       jnb 0xfd1e.0x7,0x02c3f0        -> 02c3f0
02c3e6: e148           movb RL4,#0x4                 
02c3e8: f7f89ef1       movb 0xf19e,RL4               
02c3ec: 1e05           bclr 0xfd0a.0x1               
02c3ee: 0d01           jmpr cc_UC,0x02c3f2            -> 02c3f2
02c3f0: 1f05           bset 0xfd0a.0x1               
02c3f2: e024           mov r4,#0x2                   
02c3f4: f6f494f7       mov 0xf794,r4                 
02c3f8: e6f56801       mov r5,#0x168                 
02c3fc: f6f5d2f7       mov 0xf7d2,r5                 
02c400: e012           mov r2,#0x1                   
02c402: f6f2c0f7       mov 0xf7c0,r2                 
02c406: e118           movb RL4,#0x1                 
02c408: f7f816f0       movb 0xf016,RL4               
02c40c: e6f40001       mov r4,#0x100                 
02c410: f6f4a4e9       mov 0xe9a4,r4                 
02c414: f2f39e00       mov r3,0x9e                   
02c418: f6f3a6e9       mov 0xe9a6,r3                 
02c41c: ee05           bclr 0xfd0a.0xe               
02c41e: da022ee5       calls 0x02e52e                 -> 02e52e
02c422: e6fc5419       mov r12,#0x1954               
02c426: c2fd9af1       movbz r13,0xf19a              
02c42a: da03024b       calls 0x034b02                 -> 034b02
02c42e: 9a2e0d70       jnb 0xfd5c.0x7,0x02c44c        -> 02c44c
02c432: e6fc3e19       mov r12,#0x193e               
02c436: da03a64b       calls 0x034ba6                 -> 034ba6
02c43a: f7f886db       movb 0xdb86,RL4               
02c43e: e6fc4619       mov r12,#0x1946               
02c442: da03a64b       calls 0x034ba6                 -> 034ba6
02c446: f7f887db       movb 0xdb87,RL4               
02c44a: 0d0c           jmpr cc_UC,0x02c464            -> 02c464
02c44c: e6fc5619       mov r12,#0x1956               
02c450: da03a64b       calls 0x034ba6                 -> 034ba6
02c454: f7f886db       movb 0xdb86,RL4               
02c458: e6fc4e19       mov r12,#0x194e               
02c45c: da03a64b       calls 0x034ba6                 -> 034ba6
02c460: f7f887db       movb 0xdb87,RL4               
02c464: da027289       calls 0x028972                 -> 028972
02c468: da021287       calls 0x028712                 -> 028712
02c46c: da02d489       calls 0x0289d4                 -> 0289d4
02c470: da029688       calls 0x028896                 -> 028896
02c474: da028689       calls 0x028986                 -> 028986
02c478: f78e5ee6       movb 0xe65e,0xff1c            
02c47c: 0ac7ff5e       bfldl 0xff8e,#0x5e,#0xff      
02c480: f78e5ee6       movb 0xe65e,0xff1c            
02c484: e7f8fe00       movb RL4,#0xfe                
02c488: 65f88af6       andb 0xf68a,RL4               
02c48c: f3f88af6       movb RL4,0xf68a               
02c490: 3a888836       bmovn 0xff10.0x6,0xff10.0x3   
02c494: 4a88e265       bmov 0xffc4.0x5,0xff10.0x6    
02c498: ef29           bset 0xfd52.0xe               
02c49a: f2f410ea       mov r4,0xea10                 
02c49e: 66f40800       and r4,#0x8                   
02c4a2: 3d04           jmpr cc_NE,0x02c4ac            -> 02c4ac
02c4a4: e7f8fd00       movb RL4,#0xfd                
02c4a8: 65f8e5f6       andb 0xf6e5,RL4               
02c4ac: f2f47401       mov r4,0x174                  
02c4b0: f6f470f7       mov 0xf770,r4                 
02c4b4: da0326a2       calls 0x03a226                 -> 03a226
02c4b8: 3f03           bset 0xfd06.0x3               
02c4ba: bf03           bset 0xfd06.0xb               
02c4bc: 8a210460       jb 0xfd42.0x6,0x02c4c8         -> 02c4c8
02c4c0: 8a050200       jb 0xfd0a.0x0,0x02c4c8         -> 02c4c8
02c4c4: 8a0302b0       jb 0xfd06.0xb,0x02c4cc         -> 02c4cc
02c4c8: 6f88           bset 0xff10.0x6               
02c4ca: 0d01           jmpr cc_UC,0x02c4ce            -> 02c4ce
02c4cc: 6e88           bclr 0xff10.0x6               
02c4ce: 4a88306a       bmov 0xfd60.0xa,0xff10.0x6    
02c4d2: da025687       calls 0x028756                 -> 028756
02c4d6: da02f886       calls 0x0286f8                 -> 0286f8
02c4da: da021e88       calls 0x02881e                 -> 02881e
02c4de: db00           rets                          
02c7f0: 0000           add r0,r0                     
02c7f2: e6a40000       mov 0xff48,#0x0               
02c7f6: e6a30000       mov 0xff46,#0x0               
02c7fa: e6230000       mov 0xfe46,#0x0               
02c7fe: f78e81fa       movb 0xfa81,0xff1c            
02c802: f78e82fa       movb 0xfa82,0xff1c            
02c806: f68e7efa       mov 0xfa7e,0xff1c             
02c80a: e6f400c0       mov r4,#0xc000                
02c80e: 64f400fd       and 0xfd00,r4                 
02c812: f68e02fd       mov 0xfd02,0xff1c             
02c816: f68e5efd       mov 0xfd5e,0xff1c             
02c81a: 0e31           bclr 0xfd62.0x0               
02c81c: db00           rets                          
02c880: 6a00e6c9       band 0xffcc.0x9,0xfd00.0xc    
02c884: 6b00           divl r0                       
02c886: db00           rets                          
02c8cc: f78e5ee6       movb 0xe65e,0xff1c            
02c8d0: e6e37be5       mov 0xffc6,#0xe57b            
02c8d4: f78ee7f6       movb 0xf6e7,0xff1c            
02c8d8: e7fa8000       movb RL5,#0x80                
02c8dc: 75fae4f6       orb 0xf6e4,RL5                
02c8e0: e7f6fe00       movb RL3,#0xfe                
02c8e4: 65f6e5f6       andb 0xf6e5,RL3               
02c8e8: 0d10           jmpr cc_UC,0x02c90a            -> 02c90a
02c90a: e7f82000       movb RL4,#0x20                
02c90e: 75f8e5f6       orb 0xf6e5,RL4                
02c912: e7f84000       movb RL4,#0x40                
02c916: 75f8e5f6       orb 0xf6e5,RL4                
02c91a: e118           movb RL4,#0x1                 
02c91c: 75f8e4f6       orb 0xf6e4,RL4                
02c920: 6e2b           bclr 0xfd56.0x6               
02c922: e128           movb RL4,#0x2                 
02c924: 75f8e4f6       orb 0xf6e4,RL4                
02c928: f78e76f5       movb 0xf576,0xff1c            
02c92c: e148           movb RL4,#0x4                 
02c92e: 75f8e4f6       orb 0xf6e4,RL4                
02c932: f78e77f5       movb 0xf577,0xff1c            
02c936: e128           movb RL4,#0x2                 
02c938: 75f8e5f6       orb 0xf6e5,RL4                
02c93c: ee29           bclr 0xfd52.0xe               
02c93e: e7f81000       movb RL4,#0x10                
02c942: 75f8e5f6       orb 0xf6e5,RL4                
02c946: f78eecf6       movb 0xf6ec,0xff1c            
02c94a: f78eedf6       movb 0xf6ed,0xff1c            
02c94e: f78eeef6       movb 0xf6ee,0xff1c            
02c952: f78eeff6       movb 0xf6ef,0xff1c            
02c956: db00           rets                          
02c968: e6ce4900       mov 0xff9c,#0x49              
02c96c: e6a84043       mov 0xff50,#0x4340            
02c970: db00           rets                          
02cd98: 0a8ff489       bfldl 0xfd1e,#0x89,#0xf4      
02cd9c: 3e00           bclr 0xfd00.0x3               
02cd9e: 41c8           cmpb RL6,RL4                  
02cda0: 9d04           jmpr cc_NC,0x02cdaa            -> 02cdaa
02cda2: e004           mov r4,#0x0                   
02cda4: c4495800       mov [r9+#0x58],r4             
02cda8: 0d51           jmpr cc_UC,0x02ce4c            -> 02ce4c
02cdaa: d4499400       mov r4,[r9+#0x94]             
02cdae: 42f4b401       cmp r4,0x1b4                  
02cdb2: ed46           jmpr cc_UGT,0x02ce40           -> 02ce40
02cdb4: f4893d00       movb RL4,[r9+#0x3d]           
02cdb8: 41c8           cmpb RL6,RL4                  
02cdba: fd29           jmpr cc_ULE,0x02ce0e           -> 02ce0e
02cdbc: e6fc3633       mov r12,#0x3336               
02cdc0: f2fd54fc       mov r13,0xfc54                
02cdc4: da03524b       calls 0x034b52                 -> 034b52
02cdc8: e6fc3833       mov r12,#0x3338               
02cdcc: da03a64b       calls 0x034ba6                 -> 034ba6
02cdd0: c084           movbz r4,RL4                  
02cdd2: f059           mov r5,r9                     
02cdd4: 06f59000       add r5,#0x90                  
02cdd8: a825           mov r2,[r5]                   
02cdda: 0024           add r2,r4                     
02cddc: b825           mov [r5],r2                   
02cdde: d4c98c00       mov r12,[r9+#0x8c]            
02cde2: f049           mov r4,r9                     
02cde4: 06f45800       add r4,#0x58                  
02cde8: a8d4           mov r13,[r4]                  
02cdea: 08d1           add r13,#0x1                  
02cdec: b8d4           mov [r4],r13                  
02cdee: da038444       calls 0x034484                 -> 034484
02cdf2: c4498c00       mov [r9+#0x8c],r4             
02cdf6: e004           mov r4,#0x0                   
02cdf8: c4495800       mov [r9+#0x58],r4             
02cdfc: e128           movb RL4,#0x2                 
02cdfe: e4893b00       movb [r9+#0x3b],RL4           
02ce02: d4499400       mov r4,[r9+#0x94]             
02ce06: 0841           add r4,#0x1                   
02ce08: c4499400       mov [r9+#0x94],r4             
02ce0c: 0d1f           jmpr cc_UC,0x02ce4c            -> 02ce4c
02ce0e: f4893c00       movb RL4,[r9+#0x3c]           
02ce12: 8840           mov [-r0],r4                  
02ce14: c0cc           movbz r12,RL6                 
02ce16: c2fd8003       movbz r13,0x380               
02ce1a: da030c44       calls 0x03440c                 -> 03440c
02ce1e: 9850           mov r5,[r0+]                  
02ce20: 418a           cmpb RL4,RL5                  
02ce22: fd07           jmpr cc_ULE,0x02ce32           -> 02ce32
02ce24: f049           mov r4,r9                     
02ce26: 06f45800       add r4,#0x58                  
02ce2a: a854           mov r5,[r4]                   
02ce2c: 0851           add r5,#0x1                   
02ce2e: b854           mov [r4],r5                   
02ce30: 0d0d           jmpr cc_UC,0x02ce4c            -> 02ce4c
02ce32: e004           mov r4,#0x0                   
02ce34: c4495800       mov [r9+#0x58],r4             
02ce38: e108           movb RL4,#0x0                 
02ce3a: e4893b00       movb [r9+#0x3b],RL4           
02ce3e: 0d06           jmpr cc_UC,0x02ce4c            -> 02ce4c
02ce40: e004           mov r4,#0x0                   
02ce42: c4495800       mov [r9+#0x58],r4             
02ce46: e108           movb RL4,#0x0                 
02ce48: e4893b00       movb [r9+#0x3b],RL4           
02ce4c: e4c93c00       movb [r9+#0x3c],RL6           
02ce50: 0d6d           jmpr cc_UC,0x02cf2c            -> 02cf2c
02cf2c: d4499400       mov r4,[r9+#0x94]             
02cf30: 42f4b401       cmp r4,0x1b4                  
02cf34: eaf08090       jmpa cc_ULE,0x029080           -> 029080
02cf38: d4499200       mov r4,[r9+#0x92]             
02cf3c: 42f4b401       cmp r4,0x1b4                  
02cf40: eaf08090       jmpa cc_ULE,0x029080           -> 029080
02cf44: 9f08           bset 0xfd10.0x9               
02cf46: d4498e00       mov r4,[r9+#0x8e]             
02cf4a: 7c14           shr r4,#0x1                   
02cf4c: d4598a00       mov r5,[r9+#0x8a]             
02cf50: 4054           cmp r5,r4                     
02cf52: 8d0f           jmpr cc_C,0x02cf72             -> 02cf72
02cf54: d4499000       mov r4,[r9+#0x90]             
02cf58: 7c14           shr r4,#0x1                   
02cf5a: d4598c00       mov r5,[r9+#0x8c]             
02cf5e: 4054           cmp r5,r4                     
02cf60: 8d04           jmpr cc_C,0x02cf6a             -> 02cf6a
02cf62: e128           movb RL4,#0x2                 
02cf64: f7f80eea       movb 0xea0e,RL4               
02cf68: 0d1c           jmpr cc_UC,0x02cfa2            -> 02cfa2
02cf6a: e188           movb RL4,#0x8                 
02cf6c: f7f80eea       movb 0xea0e,RL4               
02cf70: 0d18           jmpr cc_UC,0x02cfa2            -> 02cfa2
02cf72: d4499000       mov r4,[r9+#0x90]             
02cf76: 7c14           shr r4,#0x1                   
02cf78: d4598c00       mov r5,[r9+#0x8c]             
02cf7c: 4054           cmp r5,r4                     
02cf7e: 8d04           jmpr cc_C,0x02cf88             -> 02cf88
02cf80: e148           movb RL4,#0x4                 
02cf82: f7f80eea       movb 0xea0e,RL4               
02cf86: 0d0d           jmpr cc_UC,0x02cfa2            -> 02cfa2
02cf88: e6f418f0       mov r4,#0xf018                
02cf8c: 4094           cmp r9,r4                     
02cf8e: 3d05           jmpr cc_NE,0x02cf9a            -> 02cf9a
02cf90: e6f5ffdf       mov r5,#0xdfff                
02cf94: 64f510f6       and 0xf610,r5                 
02cf98: 0d04           jmpr cc_UC,0x02cfa2            -> 02cfa2
02cf9a: e6f4ffbf       mov r4,#0xbfff                
02cf9e: 64f410f6       and 0xf610,r4                 
02cfa2: e6f418f0       mov r4,#0xf018                
02cfa6: 4094           cmp r9,r4                     
02cfa8: 3d12           jmpr cc_NE,0x02cfce            -> 02cfce
02cfaa: e6fcf6ec       mov r12,#0xecf6               
02cfae: e6fd9ca9       mov r13,#0xa99c               
02cfb2: da021c7b       calls 0x027b1c                 -> 027b1c
02cfb6: 4a1c2669       bmov 0xfd4c.0x9,0xfd38.0x6    
02cfba: c2f400ed       movbz r4,0xed00               
02cfbe: 66f42000       and r4,#0x20                  
02cfc2: 2d16           jmpr cc_EQ,0x02cff0            -> 02cff0
02cfc4: e6f4ffdf       mov r4,#0xdfff                
02cfc8: 64f4f8f5       and 0xf5f8,r4                 
02cfcc: 0d11           jmpr cc_UC,0x02cff0            -> 02cff0
02cfce: e6fc02ed       mov r12,#0xed02               
02cfd2: e6fdaca9       mov r13,#0xa9ac               
02cfd6: da021c7b       calls 0x027b1c                 -> 027b1c
02cfda: 4a1c266a       bmov 0xfd4c.0xa,0xfd38.0x6    
02cfde: c2f40ced       movbz r4,0xed0c               
02cfe2: 66f42000       and r4,#0x20                  
02cfe6: 2d04           jmpr cc_EQ,0x02cff0            -> 02cff0
02cfe8: e6f4ffbf       mov r4,#0xbfff                
02cfec: 64f4f8f5       and 0xf5f8,r4                 
02cff0: f2f4b401       mov r4,0x1b4                  
02cff4: 2d37           jmpr cc_EQ,0x02d064            -> 02d064
02cff6: d4598a00       mov r5,[r9+#0x8a]             
02cffa: 7c15           shr r5,#0x1                   
02cffc: f6f50efe       mov 0xfe0e,r5                 
02d000: 5b44           divu r4                       
02d002: f2f50efe       mov r5,0xfe0e                 
02d006: c4599a00       mov [r9+#0x9a],r5             
02d00a: d4498c00       mov r4,[r9+#0x8c]             
02d00e: 7c14           shr r4,#0x1                   
02d010: f2f5b401       mov r5,0x1b4                  
02d014: f6f40efe       mov 0xfe0e,r4                 
02d018: 5b55           divu r5                       
02d01a: f2f40efe       mov r4,0xfe0e                 
02d01e: c4499c00       mov [r9+#0x9c],r4             
02d022: d4498e00       mov r4,[r9+#0x8e]             
02d026: 7c24           shr r4,#0x2                   
02d028: f2f5b401       mov r5,0x1b4                  
02d02c: f6f40efe       mov 0xfe0e,r4                 
02d030: 5b55           divu r5                       
02d032: f2f40efe       mov r4,0xfe0e                 
02d036: c4499e00       mov [r9+#0x9e],r4             
02d03a: d4499000       mov r4,[r9+#0x90]             
02d03e: 7c24           shr r4,#0x2                   
02d040: f2f5b401       mov r5,0x1b4                  
02d044: f6f40efe       mov 0xfe0e,r4                 
02d048: 5b55           divu r5                       
02d04a: f2f40efe       mov r4,0xfe0e                 
02d04e: c449a000       mov [r9+#0xa0],r4             
02d052: f4893d00       movb RL4,[r9+#0x3d]           
02d056: e489a200       movb [r9+#0xa2],RL4           
02d05a: f4893e00       movb RL4,[r9+#0x3e]           
02d05e: e489a300       movb [r9+#0xa3],RL4           
02d062: 0d0e           jmpr cc_UC,0x02d080            -> 02d080
02d064: e004           mov r4,#0x0                   
02d066: c4499a00       mov [r9+#0x9a],r4             
02d06a: c4499c00       mov [r9+#0x9c],r4             
02d06e: c4499e00       mov [r9+#0x9e],r4             
02d072: c449a000       mov [r9+#0xa0],r4             
02d076: e108           movb RL4,#0x0                 
02d078: e489a200       movb [r9+#0xa2],RL4           
02d07c: e489a300       movb [r9+#0xa3],RL4           
02d080: 9890           mov r9,[r0+]                  
02d082: 9860           mov r6,[r0+]                  
02d084: 0802           add r0,#0x2                   
02d086: db00           rets                          
02d998: e14a           movb RL5,#0x4                 
02d99a: 75fa66f0       orb 0xf066,RL5                
02d99e: 75fa12f1       orb 0xf112,RL5                
02d9a2: 0d06           jmpr cc_UC,0x02d9b0            -> 02d9b0
02d9b0: 0802           add r0,#0x2                   
02d9b2: db00           rets                          
02e52e: 4074           cmp r7,r4                     
02e530: 8d06           jmpr cc_C,0x02e53e             -> 02e53e
02e532: f3f83cfc       movb RL4,0xfc3c               
02e536: f7f8b6e9       movb 0xe9b6,RL4               
02e53a: f7f8a7da       movb 0xdaa7,RL4               
02e53e: f3f8e5e8       movb RL4,0xe8e5               
02e542: 43f8b7e9       cmpb RL4,0xe9b7               
02e546: fd05           jmpr cc_ULE,0x02e552           -> 02e552
02e548: c087           movbz r7,RL4                  
02e54a: c2f4b7e9       movbz r4,0xe9b7               
02e54e: 2074           sub r7,r4                     
02e550: 0d05           jmpr cc_UC,0x02e55c            -> 02e55c
02e552: c2f7b7e9       movbz r7,0xe9b7               
02e556: c2f4e5e8       movbz r4,0xe8e5               
02e55a: 2074           sub r7,r4                     
02e55c: c2f4a902       movbz r4,0x2a9                
02e560: 4074           cmp r7,r4                     
02e562: 8d06           jmpr cc_C,0x02e570             -> 02e570
02e564: f3f8e5e8       movb RL4,0xe8e5               
02e568: f7f8b7e9       movb 0xe9b7,RL4               
02e56c: f7f87eda       movb 0xda7e,RL4               
02e570: e6fc9c23       mov r12,#0x239c               
02e574: c2fd3cfc       movbz r13,0xfc3c              
02e578: da03ac48       calls 0x0348ac                 -> 0348ac
02e57c: e6fc9a23       mov r12,#0x239a               
02e580: c2fd94f6       movbz r13,0xf694              
02e584: da03fc48       calls 0x0348fc                 -> 0348fc
02e588: e6fc9e23       mov r12,#0x239e               
02e58c: da036a4a       calls 0x034a6a                 -> 034a6a
02e590: f3fab7e9       movb RL5,0xe9b7               
02e594: 41a8           cmpb RL5,RL4                  
02e596: 8d4e           jmpr cc_C,0x02e634             -> 02e634
02e598: 8a0a4c10       jb 0xfd14.0x1,0x02e634         -> 02e634
02e59c: 8a0a4a00       jb 0xfd14.0x0,0x02e634         -> 02e634
02e5a0: 2f0e           bset 0xfd1c.0x2               
02e5a2: e6fcf428       mov r12,#0x28f4               
02e5a6: c2fdb6e9       movbz r13,0xe9b6              
02e5aa: da03024b       calls 0x034b02                 -> 034b02
02e5ae: e6fcf628       mov r12,#0x28f6               
02e5b2: da03a64b       calls 0x034ba6                 -> 034ba6
02e5b6: f7f8dae9       movb 0xe9da,RL4               
02e5ba: e6fcfc28       mov r12,#0x28fc               
02e5be: da03a64b       calls 0x034ba6                 -> 034ba6
02e5c2: f7f8dbe9       movb 0xe9db,RL4               
02e5c6: e6fcca23       mov r12,#0x23ca               
02e5ca: c2fd3cfc       movbz r13,0xfc3c              
02e5ce: da03ac48       calls 0x0348ac                 -> 0348ac
02e5d2: e6fcc823       mov r12,#0x23c8               
02e5d6: c2fde5e8       movbz r13,0xe8e5              
02e5da: da03fc48       calls 0x0348fc                 -> 0348fc
02e5de: e6fccc23       mov r12,#0x23cc               
02e5e2: da036a4a       calls 0x034a6a                 -> 034a6a
02e5e6: c087           movbz r7,RL4                  
02e5e8: f0c7           mov r12,r7                    
02e5ea: c2fdd2e9       movbz r13,0xe9d2              
02e5ee: da0318be       calls 0x03be18                 -> 03be18
02e5f2: c084           movbz r4,RL4                  
02e5f4: 5c84           shl r4,#0x8                   
02e5f6: f074           mov r7,r4                     
02e5f8: 9a090950       jnb 0xfd12.0x5,0x02e60e        -> 02e60e
02e5fc: 8a120710       jb 0xfd24.0x1,0x02e60e         -> 02e60e
02e600: c2f487e9       movbz r4,0xe987               
02e604: 5c84           shl r4,#0x8                   
02e606: e6f50080       mov r5,#0x8000                
02e60a: 2054           sub r5,r4                     
02e60c: 0075           add r7,r5                     
02e60e: 8a0e0230       jb 0xfd1c.0x3,0x02e616         -> 02e616
02e612: 9a0e0740       jnb 0xfd1c.0x4,0x02e624        -> 02e624
02e616: c2f4a5f4       movbz r4,0xf4a5               
02e61a: 5c84           shl r4,#0x8                   
02e61c: e6f50080       mov r5,#0x8000                
02e620: 2054           sub r5,r4                     
02e622: 0075           add r7,r5                     
02e624: 46f70080       cmp r7,#0x8000                
02e628: fd02           jmpr cc_ULE,0x02e62e           -> 02e62e
02e62a: e6f70080       mov r7,#0x8000                
02e62e: f6f7dce9       mov 0xe9dc,r7                 
02e632: 0d01           jmpr cc_UC,0x02e636            -> 02e636
02e634: 2e0e           bclr 0xfd1c.0x2               
02e636: e6fca225       mov r12,#0x25a2               
02e63a: c2fdb6e9       movbz r13,0xe9b6              
02e63e: da03024b       calls 0x034b02                 -> 034b02
02e642: e6fca025       mov r12,#0x25a0               
02e646: c2fdb7e9       movbz r13,0xe9b7              
02e64a: da03284b       calls 0x034b28                 -> 034b28
02e64e: c2f4e0fa       movbz r4,0xfae0               
02e652: 5c24           shl r4,#0x2                   
02e654: 03f8e1fa       addb RL4,0xfae1               
02e658: f7f8b9e9       movb 0xe9b9,RL4               
02e65c: f3fa9ffc       movb RL5,0xfc9f               
02e660: 49a4           cmpb RL5,#0x4                 
02e662: ed60           jmpr cc_UGT,0x02e724           -> 02e724
02e664: c0a5           movbz r5,RL5                  
02e666: 5c15           shl r5,#0x1                   
02e668: 06f5aaac       add r5,#0xacaa                
02e66c: a855           mov r5,[r5]                   
02e66e: 9c05           jmpi cc_UC,[r5]               
02e724: e6fca425       mov r12,#0x25a4               
02e728: da03be4b       calls 0x034bbe                 -> 034bbe
02e72c: f7f86afc       movb 0xfc6a,RL4               
02e730: e6fc3c27       mov r12,#0x273c               
02e734: da03be4b       calls 0x034bbe                 -> 034bbe
02e738: f7f870fc       movb 0xfc70,RL4               
02e73c: e6fc9424       mov r12,#0x2494               
02e740: da03be4b       calls 0x034bbe                 -> 034bbe
02e744: c089           movbz r9,RL4                  
02e746: c2f77602       movbz r7,0x276                
02e74a: 8a0503a0       jb 0xfd0a.0xa,0x02e754         -> 02e754
02e74e: f3f8dee9       movb RL4,0xe9de               
02e752: 2d0b           jmpr cc_EQ,0x02e76a            -> 02e76a
02e754: 9a0e2480       jnb 0xfd1c.0x8,0x02e7a0        -> 02e7a0
02e758: f3f8dee9       movb RL4,0xe9de               
02e75c: 2d03           jmpr cc_EQ,0x02e764            -> 02e764
02e75e: 2981           subb RL4,#0x1                 
02e760: f7f8dee9       movb 0xe9de,RL4               
02e764: c2f77702       movbz r7,0x277                
02e768: 0d1b           jmpr cc_UC,0x02e7a0            -> 02e7a0
02e76a: f3f83dfc       movb RL4,0xfc3d               
02e76e: 4980           cmpb RL4,#0x0                 
02e770: dd03           jmpr cc_SGE,0x02e778           -> 02e778
02e772: d088           movbs r8,RL4                  
02e774: 8180           neg r8                        
02e776: 0d02           jmpr cc_UC,0x02e77c            -> 02e77c
02e778: d2f83dfc       movbs r8,0xfc3d               
02e77c: e6fce428       mov r12,#0x28e4               
02e780: da03a64b       calls 0x034ba6                 -> 034ba6
02e784: c084           movbz r4,RL4                  
02e786: 4084           cmp r8,r4                     
02e788: 8d0a           jmpr cc_C,0x02e79e             -> 02e79e
02e78a: 8f0e           bset 0xfd1c.0x8               
02e78c: e6fc2429       mov r12,#0x2924               
02e790: da03a64b       calls 0x034ba6                 -> 034ba6
02e794: f7f8dee9       movb 0xe9de,RL4               
02e798: c2f77702       movbz r7,0x277                
02e79c: 0d01           jmpr cc_UC,0x02e7a0            -> 02e7a0
02e79e: 8e0e           bclr 0xfd1c.0x8               
02e7a0: f3f8b8e9       movb RL4,0xe9b8               
02e7a4: 4980           cmpb RL4,#0x0                 
02e7a6: fd07           jmpr cc_ULE,0x02e7b6           -> 02e7b6
02e7a8: 9f0e           bset 0xfd1c.0x9               
02e7aa: 2981           subb RL4,#0x1                 
02e7ac: f7f8b8e9       movb 0xe9b8,RL4               
02e7b0: c2f77702       movbz r7,0x277                
02e7b4: 0d01           jmpr cc_UC,0x02e7b8            -> 02e7b8
02e7b6: 9e0e           bclr 0xfd1c.0x9               
02e7b8: f046           mov r4,r6                     
02e7ba: 5c14           shl r4,#0x1                   
02e7bc: d4c4a8e9       mov r12,[r4+#0xe9a8]          
02e7c0: f48676fc       movb RL4,[r6+#0xfc76]         
02e7c4: c084           movbz r4,RL4                  
02e7c6: 5c84           shl r4,#0x8                   
02e7c8: f0d4           mov r13,r4                    
02e7ca: f0e7           mov r14,r7                    
02e7cc: 5c8e           shl r14,#0x8                  
02e7ce: da03f24b       calls 0x034bf2                 -> 034bf2
02e7d2: f056           mov r5,r6                     
02e7d4: 5c15           shl r5,#0x1                   
02e7d6: c445a8e9       mov [r5+#0xe9a8],r4           
02e7da: f046           mov r4,r6                     
02e7dc: 5c14           shl r4,#0x1                   
02e7de: f4a4a9e9       movb RL5,[r4+#0xe9a9]         
02e7e2: 43fa7402       cmpb RL5,0x274                
02e7e6: fd07           jmpr cc_ULE,0x02e7f6           -> 02e7f6
02e7e8: c2f47402       movbz r4,0x274                
02e7ec: 5c84           shl r4,#0x8                   
02e7ee: f056           mov r5,r6                     
02e7f0: 5c15           shl r5,#0x1                   
02e7f2: c445a8e9       mov [r5+#0xe9a8],r4           
02e7f6: c2f77502       movbz r7,0x275                
02e7fa: f046           mov r4,r6                     
02e7fc: 5c14           shl r4,#0x1                   
02e7fe: f4a4a9e9       movb RL5,[r4+#0xe9a9]         
02e802: c0a4           movbz r4,RL5                  
02e804: 4047           cmp r4,r7                     
02e806: 9d24           jmpr cc_NC,0x02e850            -> 02e850
02e808: f047           mov r4,r7                     
02e80a: 5c84           shl r4,#0x8                   
02e80c: f056           mov r5,r6                     
02e80e: 5c15           shl r5,#0x1                   
02e810: c445a8e9       mov [r5+#0xe9a8],r4           
02e814: f3f83cfc       movb RL4,0xfc3c               
02e818: 43f8c202       cmpb RL4,0x2c2                
02e81c: fd12           jmpr cc_ULE,0x02e842           -> 02e842
02e81e: 9a0e1020       jnb 0xfd1c.0x2,0x02e842        -> 02e842
02e822: 8a2f0e80       jb 0xfd5e.0x8,0x02e842         -> 02e842
02e826: 8a2f0ca0       jb 0xfd5e.0xa,0x02e842         -> 02e842
02e82a: f2f40cea       mov r4,0xea0c                 
02e82e: 66f40010       and r4,#0x1000                
02e832: 3d07           jmpr cc_NE,0x02e842            -> 02e842
02e834: f046           mov r4,r6                     
02e836: 5c14           shl r4,#0x1                   
02e838: d45492ac       mov r5,[r4+#0xac92]           
02e83c: 75fadfe9       orb 0xe9df,RL5                
02e840: 0d0d           jmpr cc_UC,0x02e85c            -> 02e85c
02e842: f046           mov r4,r6                     
02e844: 5c14           shl r4,#0x1                   
02e846: d4549eac       mov r5,[r4+#0xac9e]           
02e84a: 65fadfe9       andb 0xe9df,RL5               
02e84e: 0d06           jmpr cc_UC,0x02e85c            -> 02e85c
02e850: f046           mov r4,r6                     
02e852: 5c14           shl r4,#0x1                   
02e854: d4549eac       mov r5,[r4+#0xac9e]           
02e858: 65fadfe9       andb 0xe9df,RL5               
02e85c: 5e0e           bclr 0xfd1c.0x5               
02e85e: 8a0e0220       jb 0xfd1c.0x2,0x02e866         -> 02e866
02e862: ea00a8ac       jmpa cc_UC,0x02aca8            -> 02aca8
02e866: 9a1902e0       jnb 0xfd32.0xe,0x02e86e        -> 02e86e
02e86a: ea0000ac       jmpa cc_UC,0x02ac00            -> 02ac00
02e86e: 9a1902f0       jnb 0xfd32.0xf,0x02e876        -> 02e876
02e872: ea0000ac       jmpa cc_UC,0x02ac00            -> 02ac00
02e876: 9a2f0280       jnb 0xfd5e.0x8,0x02e87e        -> 02e87e
02e87a: ea0000ac       jmpa cc_UC,0x02ac00            -> 02ac00
02e87e: 9a2f02a0       jnb 0xfd5e.0xa,0x02e886        -> 02e886
02e882: ea0000ac       jmpa cc_UC,0x02ac00            -> 02ac00
02e886: f2f40cea       mov r4,0xea0c                 
02e88a: 66f40010       and r4,#0x1000                
02e88e: ea3000ac       jmpa cc_NE,0x02ac00            -> 02ac00
02e892: f3f8d8e9       movb RL4,0xe9d8               
02e896: 3d05           jmpr cc_NE,0x02e8a2            -> 02e8a2
02e898: f046           mov r4,r6                     
02e89a: 5c14           shl r4,#0x1                   
02e89c: d484c0e9       mov r8,[r4+#0xe9c0]           
02e8a0: 0d12           jmpr cc_UC,0x02e8c6            -> 02e8c6
02e8a2: f046           mov r4,r6                     
02e8a4: 5c64           shl r4,#0x6                   
02e8a6: e6f540d8       mov r5,#0xd840                
02e8aa: 0054           add r5,r4                     
02e8ac: c2f4b9e9       movbz r4,0xe9b9               
02e8b0: 0054           add r5,r4                     
02e8b2: a985           movb RL4,[r5]                 
02e8b4: c088           movbz r8,RL4                  
02e8b6: 5c88           shl r8,#0x8                   
02e8b8: f3f8b9e9       movb RL4,0xe9b9               
02e8bc: e486bae9       movb [r6+#0xe9ba],RL4         
02e8c0: e118           movb RL4,#0x1                 
02e8c2: 25f8d8e9       subb 0xe9d8,RL4               
02e8c6: c2f77502       movbz r7,0x275                
02e8ca: f046           mov r4,r6                     
02e8cc: 5c14           shl r4,#0x1                   
02e8ce: f4a4a9e9       movb RL5,[r4+#0xe9a9]         
02e8d2: c0a4           movbz r4,RL5                  
02e8d4: 4047           cmp r4,r7                     
02e8d6: fd05           jmpr cc_ULE,0x02e8e2           -> 02e8e2
02e8d8: f046           mov r4,r6                     
02e8da: 5c14           shl r4,#0x1                   
02e8dc: f4a4a9e9       movb RL5,[r4+#0xe9a9]         
02e8e0: c0a7           movbz r7,RL5                  
02e8e2: e6fcf823       mov r12,#0x23f8               
02e8e6: da03a64b       calls 0x034ba6                 -> 034ba6
02e8ea: d08d           movbs r13,RL4                 
02e8ec: c0ec           movbz r12,RL7                 
02e8ee: da030e45       calls 0x03450e                 -> 03450e
02e8f2: c084           movbz r4,RL4                  
02e8f4: f074           mov r7,r4                     
02e8f6: f047           mov r4,r7                     
02e8f8: 1b49           mulu r4,r9                    
02e8fa: f2f40efe       mov r4,0xfe0e                 
02e8fe: 7c54           shr r4,#0x5                   
02e900: f074           mov r7,r4                     
02e902: 9a0e0790       jnb 0xfd1c.0x9,0x02e914        -> 02e914
02e906: c2f46802       movbz r4,0x268                
02e90a: 1b47           mulu r4,r7                    
02e90c: f2f40efe       mov r4,0xfe0e                 
02e910: 7c54           shr r4,#0x5                   
02e912: f074           mov r7,r4                     
02e914: f48676fc       movb RL4,[r6+#0xfc76]         
02e918: c084           movbz r4,RL4                  
02e91a: 4047           cmp r4,r7                     
02e91c: 8d29           jmpr cc_C,0x02e970             -> 02e970
02e91e: 5f0e           bset 0xfd1c.0x5               
02e920: f046           mov r4,r6                     
02e922: 5c14           shl r4,#0x1                   
02e924: d4547aac       mov r5,[r4+#0xac7a]           
02e928: 74f52cfd       or 0xfd2c,r5                  
02e92c: f047           mov r4,r7                     
02e92e: 5c14           shl r4,#0x1                   
02e930: f4a676fc       movb RL5,[r6+#0xfc76]         
02e934: c0a5           movbz r5,RL5                  
02e936: 4054           cmp r5,r4                     
02e938: fd09           jmpr cc_ULE,0x02e94c           -> 02e94c
02e93a: f0c8           mov r12,r8                    
02e93c: c2fddbe9       movbz r13,0xe9db              
02e940: 81d0           neg r13                       
02e942: 5c8d           shl r13,#0x8                  
02e944: da032e45       calls 0x03452e                 -> 03452e
02e948: f084           mov r8,r4                     
02e94a: 0d08           jmpr cc_UC,0x02e95c            -> 02e95c
02e94c: f0c8           mov r12,r8                    
02e94e: c2fddae9       movbz r13,0xe9da              
02e952: 81d0           neg r13                       
02e954: 5c8d           shl r13,#0x8                  
02e956: da032e45       calls 0x03452e                 -> 03452e
02e95a: f084           mov r8,r4                     
02e95c: f0c8           mov r12,r8                    
02e95e: c2fdd9e9       movbz r13,0xe9d9              
02e962: 26fd8000       sub r13,#0x80                 
02e966: 5c8d           shl r13,#0x8                  
02e968: da032e45       calls 0x03452e                 -> 03452e
02e96c: f074           mov r7,r4                     
02e96e: 0d1d           jmpr cc_UC,0x02e9aa            -> 02e9aa
02e970: 8a151210       jb 0xfd2a.0x1,0x02e998         -> 02e998
02e974: 8a1610b0       jb 0xfd2c.0xb,0x02e998         -> 02e998
02e978: e6fc0229       mov r12,#0x2902               
02e97c: da03b24b       calls 0x034bb2                 -> 034bb2
02e980: 0084           add r8,r4                     
02e982: 46f80080       cmp r8,#0x8000                
02e986: fd08           jmpr cc_ULE,0x02e998           -> 02e998
02e988: e6f80080       mov r8,#0x8000                
02e98c: f046           mov r4,r6                     
02e98e: 5c14           shl r4,#0x1                   
02e990: d45486ac       mov r5,[r4+#0xac86]           
02e994: 64f52cfd       and 0xfd2c,r5                 
02e998: f0c8           mov r12,r8                    
02e99a: c2fdd9e9       movbz r13,0xe9d9              
02e99e: 26fd8000       sub r13,#0x80                 
02e9a2: 5c8d           shl r13,#0x8                  
02e9a4: da032e45       calls 0x03452e                 -> 03452e
02e9a8: f074           mov r7,r4                     
02e9aa: 42f7dce9       cmp r7,0xe9dc                 
02e9ae: 9d02           jmpr cc_NC,0x02e9b4            -> 02e9b4
02e9b0: f2f7dce9       mov r7,0xe9dc                 
02e9b4: c2f48de9       movbz r4,0xe98d               
02e9b8: 5c84           shl r4,#0x8                   
02e9ba: 4074           cmp r7,r4                     
02e9bc: fd11           jmpr cc_ULE,0x02e9e0           -> 02e9e0
02e9be: c2f49f03       movbz r4,0x39f                
02e9c2: 5c84           shl r4,#0x8                   
02e9c4: c2f58de9       movbz r5,0xe98d               
02e9c8: 5c85           shl r5,#0x8                   
02e9ca: f027           mov r2,r7                     
02e9cc: 2025           sub r2,r5                     
02e9ce: 4024           cmp r2,r4                     
02e9d0: fd16           jmpr cc_ULE,0x02e9fe           -> 02e9fe
02e9d2: c2f78de9       movbz r7,0xe98d               
02e9d6: c2f49f03       movbz r4,0x39f                
02e9da: 0074           add r7,r4                     
02e9dc: 5c87           shl r7,#0x8                   
02e9de: 0d0f           jmpr cc_UC,0x02e9fe            -> 02e9fe
02e9e0: c2f49f03       movbz r4,0x39f                
02e9e4: 5c84           shl r4,#0x8                   
02e9e6: c2f58de9       movbz r5,0xe98d               
02e9ea: 5c85           shl r5,#0x8                   
02e9ec: 2057           sub r5,r7                     
02e9ee: 4054           cmp r5,r4                     
02e9f0: fd06           jmpr cc_ULE,0x02e9fe           -> 02e9fe
02e9f2: c2f78de9       movbz r7,0xe98d               
02e9f6: c2f49f03       movbz r4,0x39f                
02e9fa: 2074           sub r7,r4                     
02e9fc: 5c87           shl r7,#0x8                   
02e9fe: c2f4d9e9       movbz r4,0xe9d9               
02ea02: e6f88000       mov r8,#0x80                  
02ea06: 2084           sub r8,r4                     
02ea08: 5c88           shl r8,#0x8                   
02ea0a: 0087           add r8,r7                     
02ea0c: 46f80080       cmp r8,#0x8000                
02ea10: fd02           jmpr cc_ULE,0x02ea16           -> 02ea16
02ea12: e6f80080       mov r8,#0x8000                
02ea16: 8a0a2820       jb 0xfd14.0x2,0x02ea6a         -> 02ea6a
02ea1a: 8a0a2610       jb 0xfd14.0x1,0x02ea6a         -> 02ea6a
02ea1e: 8a1624b0       jb 0xfd2c.0xb,0x02ea6a         -> 02ea6a
02ea22: 8a1a2200       jb 0xfd34.0x0,0x02ea6a         -> 02ea6a
02ea26: 8a1a2010       jb 0xfd34.0x1,0x02ea6a         -> 02ea6a
02ea2a: f2f41cfd       mov r4,0xfd1c                 
02ea2e: 66f40063       and r4,#0x6300                
02ea32: 3d1b           jmpr cc_NE,0x02ea6a            -> 02ea6a
02ea34: f2f430fd       mov r4,0xfd30                 
02ea38: 66f46f18       and r4,#0x186f                
02ea3c: 3d16           jmpr cc_NE,0x02ea6a            -> 02ea6a
02ea3e: f2f432fd       mov r4,0xfd32                 
02ea42: 66f4ffcf       and r4,#0xcfff                
02ea46: 3d11           jmpr cc_NE,0x02ea6a            -> 02ea6a
02ea48: 8a290fb0       jb 0xfd52.0xb,0x02ea6a         -> 02ea6a
02ea4c: e6fcd228       mov r12,#0x28d2               
02ea50: da03a64b       calls 0x034ba6                 -> 034ba6
02ea54: f3fae5e8       movb RL5,0xe8e5               
02ea58: 41a8           cmpb RL5,RL4                  
02ea5a: 8d07           jmpr cc_C,0x02ea6a             -> 02ea6a
02ea5c: 9a090250       jnb 0xfd12.0x5,0x02ea64        -> 02ea64
02ea60: 9a120310       jnb 0xfd24.0x1,0x02ea6a        -> 02ea6a
02ea64: f3f87002       movb RL4,0x270                
02ea68: 2d02           jmpr cc_EQ,0x02ea6e            -> 02ea6e
02ea6a: ae0e           bclr 0xfd1c.0xa               
02ea6c: 0d0d           jmpr cc_UC,0x02ea88            -> 02ea88
02ea6e: af0e           bset 0xfd1c.0xa               
02ea70: f048           mov r4,r8                     
02ea72: 7c84           shr r4,#0x8                   
02ea74: f056           mov r5,r6                     
02ea76: 5c65           shl r5,#0x6                   
02ea78: e6f240d8       mov r2,#0xd840                
02ea7c: 0025           add r2,r5                     
02ea7e: f4a6bae9       movb RL5,[r6+#0xe9ba]         
02ea82: c0a5           movbz r5,RL5                  
02ea84: 0025           add r2,r5                     
02ea86: b982           movb [r2],RL4                 
02ea88: f486bae9       movb RL4,[r6+#0xe9ba]         
02ea8c: f3fab9e9       movb RL5,0xe9b9               
02ea90: 41a8           cmpb RL5,RL4                  
02ea92: ea203cad       jmpa cc_EQ,0x02ad3c            -> 02ad3c
02ea96: c2f49a03       movbz r4,0x39a                
02ea9a: 5c84           shl r4,#0x8                   
02ea9c: 8840           mov [-r0],r4                  
02ea9e: f046           mov r4,r6                     
02eaa0: 5c64           shl r4,#0x6                   
02eaa2: e6f540d8       mov r5,#0xd840                
02eaa6: 0054           add r5,r4                     
02eaa8: c2f4b9e9       movbz r4,0xe9b9               
02eaac: 0054           add r5,r4                     
02eaae: a985           movb RL4,[r5]                 
02eab0: c084           movbz r4,RL4                  
02eab2: 5c84           shl r4,#0x8                   
02eab4: f074           mov r7,r4                     
02eab6: f0c4           mov r12,r4                    
02eab8: f0d8           mov r13,r8                    
02eaba: da039444       calls 0x034494                 -> 034494
02eabe: 9850           mov r5,[r0+]                  
02eac0: 4045           cmp r4,r5                     
02eac2: fd58           jmpr cc_ULE,0x02eb74           -> 02eb74
02eac4: 4078           cmp r7,r8                     
02eac6: fd05           jmpr cc_ULE,0x02ead2           -> 02ead2
02eac8: c2f49a03       movbz r4,0x39a                
02eacc: 5c84           shl r4,#0x8                   
02eace: 0084           add r8,r4                     
02ead0: 0d04           jmpr cc_UC,0x02eada            -> 02eada
02ead2: c2f49a03       movbz r4,0x39a                
02ead6: 5c84           shl r4,#0x8                   
02ead8: 2084           sub r8,r4                     
02eada: f0c8           mov r12,r8                    
02eadc: c2fdd9e9       movbz r13,0xe9d9              
02eae0: 26fd8000       sub r13,#0x80                 
02eae4: 5c8d           shl r13,#0x8                  
02eae6: da032e45       calls 0x03452e                 -> 03452e
02eaea: f074           mov r7,r4                     
02eaec: 42f7dce9       cmp r7,0xe9dc                 
02eaf0: 9d02           jmpr cc_NC,0x02eaf6            -> 02eaf6
02eaf2: f2f7dce9       mov r7,0xe9dc                 
02eaf6: c2f48de9       movbz r4,0xe98d               
02eafa: 5c84           shl r4,#0x8                   
02eafc: 4074           cmp r7,r4                     
02eafe: fd11           jmpr cc_ULE,0x02eb22           -> 02eb22
02eb00: c2f49f03       movbz r4,0x39f                
02eb04: 5c84           shl r4,#0x8                   
02eb06: c2f58de9       movbz r5,0xe98d               
02eb0a: 5c85           shl r5,#0x8                   
02eb0c: f027           mov r2,r7                     
02eb0e: 2025           sub r2,r5                     
02eb10: 4024           cmp r2,r4                     
02eb12: fd16           jmpr cc_ULE,0x02eb40           -> 02eb40
02eb14: c2f78de9       movbz r7,0xe98d               
02eb18: c2f49f03       movbz r4,0x39f                
02eb1c: 0074           add r7,r4                     
02eb1e: 5c87           shl r7,#0x8                   
02eb20: 0d0f           jmpr cc_UC,0x02eb40            -> 02eb40
02eb22: c2f49f03       movbz r4,0x39f                
02eb26: 5c84           shl r4,#0x8                   
02eb28: c2f58de9       movbz r5,0xe98d               
02eb2c: 5c85           shl r5,#0x8                   
02eb2e: 2057           sub r5,r7                     
02eb30: 4054           cmp r5,r4                     
02eb32: fd06           jmpr cc_ULE,0x02eb40           -> 02eb40
02eb34: c2f78de9       movbz r7,0xe98d               
02eb38: c2f49f03       movbz r4,0x39f                
02eb3c: 2074           sub r7,r4                     
02eb3e: 5c87           shl r7,#0x8                   
02eb40: c2f4d9e9       movbz r4,0xe9d9               
02eb44: e6f88000       mov r8,#0x80                  
02eb48: 2084           sub r8,r4                     
02eb4a: 5c88           shl r8,#0x8                   
02eb4c: 0087           add r8,r7                     
02eb4e: 46f80080       cmp r8,#0x8000                
02eb52: fd02           jmpr cc_ULE,0x02eb58           -> 02eb58
02eb54: e6f80080       mov r8,#0x8000                
02eb58: 9a0e4ca0       jnb 0xfd1c.0xa,0x02ebf4        -> 02ebf4
02eb5c: f048           mov r4,r8                     
02eb5e: 7c84           shr r4,#0x8                   
02eb60: f056           mov r5,r6                     
02eb62: 5c65           shl r5,#0x6                   
02eb64: e6f240d8       mov r2,#0xd840                
02eb68: 0025           add r2,r5                     
02eb6a: c2f5b9e9       movbz r5,0xe9b9               
02eb6e: 0025           add r2,r5                     
02eb70: b982           movb [r2],RL4                 
02eb72: 0d40           jmpr cc_UC,0x02ebf4            -> 02ebf4
02eb74: f087           mov r8,r7                     
02eb76: f0c8           mov r12,r8                    
02eb78: c2fdd9e9       movbz r13,0xe9d9              
02eb7c: 26fd8000       sub r13,#0x80                 
02eb80: 5c8d           shl r13,#0x8                  
02eb82: da032e45       calls 0x03452e                 -> 03452e
02eb86: f074           mov r7,r4                     
02eb88: 42f7dce9       cmp r7,0xe9dc                 
02eb8c: 9d02           jmpr cc_NC,0x02eb92            -> 02eb92
02eb8e: f2f7dce9       mov r7,0xe9dc                 
02eb92: c2f48de9       movbz r4,0xe98d               
02eb96: 5c84           shl r4,#0x8                   
02eb98: 4074           cmp r7,r4                     
02eb9a: fd11           jmpr cc_ULE,0x02ebbe           -> 02ebbe
02eb9c: c2f49f03       movbz r4,0x39f                
02eba0: 5c84           shl r4,#0x8                   
02eba2: c2f58de9       movbz r5,0xe98d               
02eba6: 5c85           shl r5,#0x8                   
02eba8: f027           mov r2,r7                     
02ebaa: 2025           sub r2,r5                     
02ebac: 4024           cmp r2,r4                     
02ebae: fd16           jmpr cc_ULE,0x02ebdc           -> 02ebdc
02ebb0: c2f78de9       movbz r7,0xe98d               
02ebb4: c2f49f03       movbz r4,0x39f                
02ebb8: 0074           add r7,r4                     
02ebba: 5c87           shl r7,#0x8                   
02ebbc: 0d0f           jmpr cc_UC,0x02ebdc            -> 02ebdc
02ebbe: c2f49f03       movbz r4,0x39f                
02ebc2: 5c84           shl r4,#0x8                   
02ebc4: c2f58de9       movbz r5,0xe98d               
02ebc8: 5c85           shl r5,#0x8                   
02ebca: 2057           sub r5,r7                     
02ebcc: 4054           cmp r5,r4                     
02ebce: fd06           jmpr cc_ULE,0x02ebdc           -> 02ebdc
02ebd0: c2f78de9       movbz r7,0xe98d               
02ebd4: c2f49f03       movbz r4,0x39f                
02ebd8: 2074           sub r7,r4                     
02ebda: 5c87           shl r7,#0x8                   
02ebdc: c2f4d9e9       movbz r4,0xe9d9               
02ebe0: e6f88000       mov r8,#0x80                  
02ebe4: 2084           sub r8,r4                     
02ebe6: 5c88           shl r8,#0x8                   
02ebe8: 0087           add r8,r7                     
02ebea: 46f80080       cmp r8,#0x8000                
02ebee: fd02           jmpr cc_ULE,0x02ebf4           -> 02ebf4
02ebf0: e6f80080       mov r8,#0x8000                
02ebf4: f3f8b9e9       movb RL4,0xe9b9               
02ebf8: e486bae9       movb [r6+#0xe9ba],RL4         
02ebfc: ea003cad       jmpa cc_UC,0x02ad3c            -> 02ad3c
02ed4a: c0e9           movbz r9,RL7                  
02ed4c: f047           mov r4,r7                     
02ed4e: 7c84           shr r4,#0x8                   
02ed50: 06f48000       add r4,#0x80                  
02ed54: c084           movbz r4,RL4                  
02ed56: f4a62edc       movb RL5,[r6+#0xdc2e]         
02ed5a: d0a5           movbs r5,RL5                  
02ed5c: 0045           add r4,r5                     
02ed5e: f074           mov r7,r4                     
02ed60: f4862edc       movb RL4,[r6+#0xdc2e]         
02ed64: 4980           cmpb RL4,#0x0                 
02ed66: dd08           jmpr cc_SGE,0x02ed78           -> 02ed78
02ed68: 46f78000       cmp r7,#0x80                  
02ed6c: 9d05           jmpr cc_NC,0x02ed78            -> 02ed78
02ed6e: e7f88100       movb RL4,#0x81                
02ed72: e4867cfc       movb [r6+#0xfc7c],RL4         
02ed76: 0d02           jmpr cc_UC,0x02ed7c            -> 02ed7c
02ed78: e4e67cfc       movb [r6+#0xfc7c],RL7         
02ed7c: f4867cfc       movb RL4,[r6+#0xfc7c]         
02ed80: b180           cplb RL4                      
02ed82: e4867cfc       movb [r6+#0xfc7c],RL4         
02ed86: 0d09           jmpr cc_UC,0x02ed9a            -> 02ed9a
02ed9a: 9890           mov r9,[r0+]                  
02ed9c: 9880           mov r8,[r0+]                  
02ed9e: 9870           mov r7,[r0+]                  
02eda0: 9860           mov r6,[r0+]                  
02eda2: db00           rets                          
02ef6e: 06fc0080       add r12,#0x8000               
02ef72: da036245       calls 0x034562                 -> 034562
02ef76: 8840           mov [-r0],r4                  
02ef78: f2fcd8f0       mov r12,0xf0d8                
02ef7c: c2f4fdf0       movbz r4,0xf0fd               
02ef80: 5c84           shl r4,#0x8                   
02ef82: f0d4           mov r13,r4                    
02ef84: da031a46       calls 0x03461a                 -> 03461a
02ef88: f0c4           mov r12,r4                    
02ef8a: 98d0           mov r13,[r0+]                 
02ef8c: da036245       calls 0x034562                 -> 034562
02ef90: 06f40080       add r4,#0x8000                
02ef94: f6f4daf0       mov 0xf0da,r4                 
02ef98: da02583a       calls 0x023a58                 -> 023a58
02ef9c: f7f8c8fa       movb 0xfac8,RL4               
02efa0: db00           rets                          
02f564: f1c8           movb RL6,RL4                  
02f566: c0cc           movbz r12,RL6                 
02f568: c2fd7c03       movbz r13,0x37c               
02f56c: da03b846       calls 0x0346b8                 -> 0346b8
02f570: c08d           movbz r13,RL4                 
02f572: f4892900       movb RL4,[r9+#0x29]           
02f576: c08c           movbz r12,RL4                 
02f578: da030c44       calls 0x03440c                 -> 03440c
02f57c: e4892c00       movb [r9+#0x2c],RL4           
02f580: c0cc           movbz r12,RL6                 
02f582: c2fd7d03       movbz r13,0x37d               
02f586: da03b846       calls 0x0346b8                 -> 0346b8
02f58a: c08d           movbz r13,RL4                 
02f58c: f4892900       movb RL4,[r9+#0x29]           
02f590: c08c           movbz r12,RL4                 
02f592: da030c44       calls 0x03440c                 -> 03440c
02f596: e4892d00       movb [r9+#0x2d],RL4           
02f59a: 9890           mov r9,[r0+]                  
02f59c: 9860           mov r6,[r0+]                  
02f59e: db00           rets                          
02f5b2: d833           mov [r3+],[r3]                
02f5b4: c2fd0be8       movbz r13,0xe80b              
02f5b8: da03fc48       calls 0x0348fc                 -> 0348fc
02f5bc: e6fca811       mov r12,#0x11a8               
02f5c0: da036a4a       calls 0x034a6a                 -> 034a6a
02f5c4: f7f811e8       movb 0xe811,RL4               
02f5c8: e6fccc47       mov r12,#0x47cc               
02f5cc: c08d           movbz r13,RL4                 
02f5ce: da03ac48       calls 0x0348ac                 -> 0348ac
02f5d2: e6fcc333       mov r12,#0x33c3               
02f5d6: da03344a       calls 0x034a34                 -> 034a34
02f5da: f7f810e8       movb 0xe810,RL4               
02f5de: db00           rets                          
02f66c: f2f452fc       mov r4,0xfc52                 
02f670: f6f40ae8       mov 0xe80a,r4                 
02f674: c2f5bf33       movbz r5,0x33bf               
02f678: 69a3           andb RL5,#0x3                 
02f67a: 49a1           cmpb RL5,#0x1                 
02f67c: 2d08           jmpr cc_EQ,0x02f68e            -> 02f68e
02f67e: 49a2           cmpb RL5,#0x2                 
02f680: 2d04           jmpr cc_EQ,0x02f68a            -> 02f68a
02f682: 49a3           cmpb RL5,#0x3                 
02f684: 3d13           jmpr cc_NE,0x02f6ac            -> 02f6ac
02f686: 8af406d0       jb r4.0xd,0x02f696             -> 02f696
02f68a: 8af404e0       jb r4.0xe,0x02f696             -> 02f696
02f68e: 8af402f0       jb r4.0xf,0x02f696             -> 02f696
02f692: 4c45           shl r4,r5                     
02f694: 0d02           jmpr cc_UC,0x02f69a            -> 02f69a
02f696: e6f4ffff       mov r4,#0xffff                
02f69a: f6f452fc       mov 0xfc52,r4                 
02f69e: f0c4           mov r12,r4                    
02f6a0: f2fd3afc       mov r13,0xfc3a                
02f6a4: da03ce46       calls 0x0346ce                 -> 0346ce
02f6a8: f6f454fc       mov 0xfc54,r4                 
02f6ac: db00           rets                          
03069e: 0efe           bclr r14.0x0                  
0306a0: f2f40efe       mov r4,0xfe0e                 
0306a4: 46f40080       cmp r4,#0x8000                
0306a8: 9d04           jmpr cc_NC,0x0306b2            -> 0306b2
0306aa: f2f40efe       mov r4,0xfe0e                 
0306ae: 7c74           shr r4,#0x7                   
0306b0: db00           rets                          
0306b2: e7f8ff00       movb RL4,#0xff                
0306b6: db00           rets                          
0306ce: f04c           mov r4,r12                    
0306d0: 1b4d           mulu r4,r13                   
0306d2: f2f40efe       mov r4,0xfe0e                 
0306d6: f2f40cfe       mov r4,0xfe0c                 
0306da: db00           rets                          
0306e6: f2071eff       mov 0xfe0e,0xff1e             
0306ea: f2f40efe       mov r4,0xfe0e                 
0306ee: db00           rets                          
030ba8: e0fa           mov r10,#0xf                  
030baa: 001c           add r1,r12                    
030bac: e109           movb RH4,#0x0                 
030bae: a981           movb RL4,[r1]                 
030bb0: db00           rets                          
030c42: c2f45df5       movbz r4,0xf55d               
030c46: 02f49af6       add r4,0xf69a                 
030c4a: 66f40f00       and r4,#0xf                   
030c4e: f1c8           movb RL6,RL4                  
030c50: 9a291890       jnb 0xfd52.0x9,0x030c84        -> 030c84
030c54: c0c4           movbz r4,RL6                  
030c56: 5c24           shl r4,#0x2                   
030c58: e6f59cf6       mov r5,#0xf69c                
030c5c: 0054           add r5,r4                     
030c5e: 98c5           mov r12,[r5+]                 
030c60: a8d5           mov r13,[r5]                  
030c62: f2f49af6       mov r4,0xf69a                 
030c66: 5c24           shl r4,#0x2                   
030c68: e6f59cf6       mov r5,#0xf69c                
030c6c: 0054           add r5,r4                     
030c6e: 98e5           mov r14,[r5+]                 
030c70: a8f5           mov r15,[r5]                  
030c72: da03b244       calls 0x0344b2                 -> 0344b2
030c76: f6f460f5       mov 0xf560,r4                 
030c7a: 42f464f5       cmp r4,0xf564                 
030c7e: ed44           jmpr cc_UGT,0x030d08           -> 030d08
030c80: 9e29           bclr 0xfd52.0x9               
030c82: 0d42           jmpr cc_UC,0x030d08            -> 030d08
030c84: c0c4           movbz r4,RL6                  
030c86: 5c24           shl r4,#0x2                   
030c88: e6f59cf6       mov r5,#0xf69c                
030c8c: 0054           add r5,r4                     
030c8e: 98c5           mov r12,[r5+]                 
030c90: a8d5           mov r13,[r5]                  
030c92: f2f49af6       mov r4,0xf69a                 
030c96: 5c24           shl r4,#0x2                   
030c98: e6f59cf6       mov r5,#0xf69c                
030c9c: 0054           add r5,r4                     
030c9e: 98e5           mov r14,[r5+]                 
030ca0: a8f5           mov r15,[r5]                  
030ca2: da03b244       calls 0x0344b2                 -> 0344b2
030ca6: f6f462f5       mov 0xf562,r4                 
030caa: 42f464f5       cmp r4,0xf564                 
030cae: ed2b           jmpr cc_UGT,0x030d06           -> 030d06
030cb0: f2fc60f5       mov r12,0xf560                
030cb4: f0d4           mov r13,r4                    
030cb6: da039444       calls 0x034494                 -> 034494
030cba: f0c4           mov r12,r4                    
030cbc: f2fd60f5       mov r13,0xf560                
030cc0: da034447       calls 0x034744                 -> 034744
030cc4: f0d4           mov r13,r4                    
030cc6: f2fc66f5       mov r12,0xf566                
030cca: f2fe68f5       mov r14,0xf568                
030cd0: f24bf6f4       mov 0xfe96,0xf4f6             
030cd4: 66f542f4       and r5,#0xf442                
030cd8: 6af5fd10       band r13.0x0,r5.0x1           
030cdc: f2f57ef7       mov r5,0xf77e                 
030ce0: 42f56cf5       cmp r5,0xf56c                 
030ce4: 9d05           jmpr cc_NC,0x030cf0            -> 030cf0
030ce6: f2f26cf5       mov r2,0xf56c                 
030cea: f6f27ef7       mov 0xf77e,r2                 
030cee: 8f29           bset 0xfd52.0x8               
030cf0: 8a290420       jb 0xfd52.0x2,0x030cfc         -> 030cfc
030cf4: e014           mov r4,#0x1                   
030cf6: 04f45ef5       add 0xf55e,r4                 
030cfa: 2f29           bset 0xfd52.0x2               
030cfc: f2f462f5       mov r4,0xf562                 
030d00: f6f460f5       mov 0xf560,r4                 
030d04: 0d01           jmpr cc_UC,0x030d08            -> 030d08
030d06: 9f29           bset 0xfd52.0x9               
030d08: c0c4           movbz r4,RL6                  
030d0a: f6f49af6       mov 0xf69a,r4                 
030d0e: c2f45df5       movbz r4,0xf55d               
030d12: f2f598f6       mov r5,0xf698                 
030d16: 22f59af6       sub r5,0xf69a                 
030d1a: 66f50f00       and r5,#0xf                   
030d1e: 4054           cmp r5,r4                     
030d20: ed90           jmpr cc_UGT,0x030c42           -> 030c42
030d22: 0d04           jmpr cc_UC,0x030d2c            -> 030d2c
030d2c: 9860           mov r6,[r0+]                  
030d2e: db00           rets                          
031190: e6fc6e06       mov r12,#0x66e                
031194: c2fd9dfc       movbz r13,0xfc9d              
031198: da03ac48       calls 0x0348ac                 -> 0348ac
03119c: 8a0508f0       jb 0xfd0a.0xf,0x0311b0         -> 0311b0
0311a0: e6fcc206       mov r12,#0x6c2                
0311a4: da03344a       calls 0x034a34                 -> 034a34
0311a8: c084           movbz r4,RL4                  
0311aa: 5c34           shl r4,#0x3                   
0311ac: f6f48ef6       mov 0xf68e,r4                 
0311b0: e6fc7006       mov r12,#0x670                
0311b4: da03504a       calls 0x034a50                 -> 034a50
0311b8: f0c4           mov r12,r4                    
0311ba: f3f823dc       movb RL4,0xdc23               
0311be: d08d           movbs r13,RL4                 
0311c0: 5c4d           shl r13,#0x4                  
0311c2: da032e45       calls 0x03452e                 -> 03452e
0311c6: f6f458fc       mov 0xfc58,r4                 
0311ca: 46f4f00f       cmp r4,#0xff0                 
0311ce: ed04           jmpr cc_UGT,0x0311d8           -> 0311d8
0311d0: 7c44           shr r4,#0x4                   
0311d2: f7f849da       movb 0xda49,RL4               
0311d6: 0d02           jmpr cc_UC,0x0311dc            -> 0311dc
0311d8: f78f49da       movb 0xda49,0xff1e            
0311dc: 8a0a0200       jb 0xfd14.0x0,0x0311e4         -> 0311e4
0311e0: 9a0a1610       jnb 0xfd14.0x1,0x031210        -> 031210
0311e4: f3f894f6       movb RL4,0xf694               
0311e8: 43f83603       cmpb RL4,0x336                
0311ec: 9d11           jmpr cc_NC,0x031210            -> 031210
0311ee: f3fa9dfc       movb RL5,0xfc9d               
0311f2: 43fa7903       cmpb RL5,0x379                
0311f6: ed0c           jmpr cc_UGT,0x031210           -> 031210
0311f8: f3f60102       movb RL3,0x201                
0311fc: f7f668fc       movb 0xfc68,RL3               
031200: e6fc5e06       mov r12,#0x65e                
031204: da03504a       calls 0x034a50                 -> 034a50
031208: f6f466fc       mov 0xfc66,r4                 
03120c: 6f30           bset 0xfd60.0x6               
03120e: 0d01           jmpr cc_UC,0x031212            -> 031212
031210: 6e30           bclr 0xfd60.0x6               
031212: f3f89dfc       movb RL4,0xfc9d               
031216: f7f85bda       movb 0xda5b,RL4               
03121a: db00           rets                          
031270: f049           mov r4,r9                     
031272: 5c14           shl r4,#0x1                   
031274: d4c45afc       mov r12,[r4+#0xfc5a]          
031278: f2fdc401       mov r13,0x1c4                 
03127c: da038444       calls 0x034484                 -> 034484
031280: f059           mov r5,r9                     
031282: 5c15           shl r5,#0x1                   
031284: c4455afc       mov [r5+#0xfc5a],r4           
031288: f049           mov r4,r9                     
03128a: 5c14           shl r4,#0x1                   
03128c: d4545afc       mov r5,[r4+#0xfc5a]           
031290: 42f5c001       cmp r5,0x1c0                  
031294: fd06           jmpr cc_ULE,0x0312a2           -> 0312a2
031296: f2f4c001       mov r4,0x1c0                  
03129a: f059           mov r5,r9                     
03129c: 5c15           shl r5,#0x1                   
03129e: c4455afc       mov [r5+#0xfc5a],r4           
0312a2: f049           mov r4,r9                     
0312a4: 5c14           shl r4,#0x1                   
0312a6: d4c45afc       mov r12,[r4+#0xfc5a]          
0312aa: 7c1c           shr r12,#0x1                  
0312ac: f2fd5afc       mov r13,0xfc5a                
0312b0: da034447       calls 0x034744                 -> 034744
0312b4: f059           mov r5,r9                     
0312b6: 5c15           shl r5,#0x1                   
0312b8: c44596e9       mov [r5+#0xe996],r4           
0312bc: 9890           mov r9,[r0+]                  
0312be: db00           rets                          
0314a8: f3fa94f6       movb RL5,0xf694               
0314ac: 43fa3603       cmpb RL5,0x336                
0314b0: 9d07           jmpr cc_NC,0x0314c0            -> 0314c0
0314b2: f3f89dfc       movb RL4,0xfc9d               
0314b6: 43f87903       cmpb RL4,0x379                
0314ba: ed02           jmpr cc_UGT,0x0314c0           -> 0314c0
0314bc: 6f88           bset 0xff10.0x6               
0314be: 0d01           jmpr cc_UC,0x0314c2            -> 0314c2
0314c0: 6e88           bclr 0xff10.0x6               
0314c2: 4a883066       bmov 0xfd60.0x6,0xff10.0x6    
0314c6: c2f49903       movbz r4,0x399                
0314ca: f054           mov r5,r4                     
0314cc: 5c25           shl r5,#0x2                   
0314ce: 2054           sub r5,r4                     
0314d0: 5c15           shl r5,#0x1                   
0314d2: f7faa2e9       movb 0xe9a2,RL5               
0314d6: 8e15           bclr 0xfd2a.0x8               
0314d8: e6f40080       mov r4,#0x8000                
0314dc: f6f45afc       mov 0xfc5a,r4                 
0314e0: e6f50080       mov r5,#0x8000                
0314e4: f6f596e9       mov 0xe996,r5                 
0314e8: e019           mov r9,#0x1                   
0314ea: e108           movb RL4,#0x0                 
0314ec: f059           mov r5,r9                     
0314ee: 5c15           shl r5,#0x1                   
0314f0: e48596e9       movb [r5+#0xe996],RL4         
0314f4: f049           mov r4,r9                     
0314f6: 5c14           shl r4,#0x1                   
0314f8: d4c496e9       mov r12,[r4+#0xe996]          
0314fc: f2fdc201       mov r13,0x1c2                 
031500: f2fec001       mov r14,0x1c0                 
031504: da030a48       calls 0x03480a                 -> 03480a
031508: f059           mov r5,r9                     
03150a: 5c15           shl r5,#0x1                   
03150c: c4455afc       mov [r5+#0xfc5a],r4           
031510: 0891           add r9,#0x1                   
031512: 4896           cmp r9,#0x6                   
031514: 8dea           jmpr cc_C,0x0314ea             -> 0314ea
031516: 9890           mov r9,[r0+]                  
031518: db00           rets                          
031660: 3cfc           ror r12,#0xf                  
031662: da03024b       calls 0x034b02                 -> 034b02
031666: e6fc6e05       mov r12,#0x56e                
03166a: c2fde5e8       movbz r13,0xe8e5              
03166e: da03284b       calls 0x034b28                 -> 034b28
031672: e6fc7205       mov r12,#0x572                
031676: da03d84b       calls 0x034bd8                 -> 034bd8
03167a: f059           mov r5,r9                     
03167c: 5c25           shl r5,#0x2                   
03167e: d42582fc       mov r2,[r5+#0xfc82]           
031682: 4024           cmp r2,r4                     
031684: 9d07           jmpr cc_NC,0x031694            -> 031694
031686: e128           movb RL4,#0x2                 
031688: f7f80eea       movb 0xea0e,RL4               
03168c: e014           mov r4,#0x1                   
03168e: 4c49           shl r4,r9                     
031690: 75f840f5       orb 0xf540,RL4                
031694: f049           mov r4,r9                     
031696: 4845           cmp r4,#0x5                   
031698: eae0ae57       jmpa cc_UGT,0x0357ae           -> 0357ae
03169c: 5c14           shl r4,#0x1                   
03169e: 06f40aad       add r4,#0xad0a                
0316a2: a844           mov r4,[r4]                   
0316a4: 9c04           jmpi cc_UC,[r4]               
031b40: 1c7b           rol r11,#0x7                  
031b42: 4a1c2867       bmov 0xfd50.0x7,0xfd38.0x6    
031b46: c2f468ee       movbz r4,0xee68               
031b4a: 66f42000       and r4,#0x20                  
031b4e: 2d0d           jmpr cc_EQ,0x031b6a            -> 031b6a
031b50: e6f4fff7       mov r4,#0xf7ff                
031b54: 64f4fcf5       and 0xf5fc,r4                 
031b58: 0d08           jmpr cc_UC,0x031b6a            -> 031b6a
031b6a: f3f894f6       movb RL4,0xf694               
031b6e: f7f85ada       movb 0xda5a,RL4               
031b72: 9890           mov r9,[r0+]                  
031b74: 9880           mov r8,[r0+]                  
031b76: 9870           mov r7,[r0+]                  
031b78: 9860           mov r6,[r0+]                  
031b7a: db00           rets                          
031bd4: f7f8cde8       movb 0xe8cd,RL4               
031bd8: 9a200bf0       jnb 0xfd40.0xf,0x031bf2        -> 031bf2
031bdc: f3fa94f6       movb RL5,0xf694               
031be0: 43fa4203       cmpb RL5,0x342                
031be4: 9d05           jmpr cc_NC,0x031bf0            -> 031bf0
031be6: f3f69af1       movb RL3,0xf19a               
031bea: 43f65503       cmpb RL3,0x355                
031bee: 8d01           jmpr cc_C,0x031bf2             -> 031bf2
031bf0: fe20           bclr 0xfd40.0xf               
031bf2: f3f894f6       movb RL4,0xf694               
031bf6: 43f83503       cmpb RL4,0x335                
031bfa: 9d05           jmpr cc_NC,0x031c06            -> 031c06
031bfc: 43f83a03       cmpb RL4,0x33a                
031c00: fd02           jmpr cc_ULE,0x031c06           -> 031c06
031c02: 7f2a           bset 0xfd54.0x7               
031c04: 0d01           jmpr cc_UC,0x031c08            -> 031c08
031c06: 7e2a           bclr 0xfd54.0x7               
031c08: f3f894f6       movb RL4,0xf694               
031c0c: 43f83c03       cmpb RL4,0x33c                
031c10: fd07           jmpr cc_ULE,0x031c20           -> 031c20
031c12: e7fabf00       movb RL5,#0xbf                
031c16: 65fa57f0       andb 0xf057,RL5               
031c1a: 65fa03f1       andb 0xf103,RL5               
031c1e: 0d06           jmpr cc_UC,0x031c2c            -> 031c2c
031c20: e7f84000       movb RL4,#0x40                
031c24: 75f857f0       orb 0xf057,RL4                
031c28: 75f803f1       orb 0xf103,RL4                
031c2c: 8a2a0460       jb 0xfd54.0x6,0x031c38         -> 031c38
031c30: 8a0a0210       jb 0xfd14.0x1,0x031c38         -> 031c38
031c34: 9a0a2600       jnb 0xfd14.0x0,0x031c84        -> 031c84
031c38: e6fc9631       mov r12,#0x3196               
031c3c: c2fd94f6       movbz r13,0xf694              
031c40: da03ac48       calls 0x0348ac                 -> 0348ac
031c44: e6fc9831       mov r12,#0x3198               
031c48: da03344a       calls 0x034a34                 -> 034a34
031c4c: f7f874f5       movb 0xf574,RL4               
031c50: e6fc8e31       mov r12,#0x318e               
031c54: da03344a       calls 0x034a34                 -> 034a34
031c58: f7f875f5       movb 0xf575,RL4               
031c5c: e6fcc630       mov r12,#0x30c6               
031c60: c2fd94f6       movbz r13,0xf694              
031c64: da03ac48       calls 0x0348ac                 -> 0348ac
031c68: e6fcc430       mov r12,#0x30c4               
031c6c: f2fdc8e8       mov r13,0xe8c8                
031c70: da039e49       calls 0x03499e                 -> 03499e
031c74: e6fcc830       mov r12,#0x30c8               
031c78: da03b84a       calls 0x034ab8                 -> 034ab8
031c7c: 06f40008       add r4,#0x800                 
031c80: f6f472f5       mov 0xf572,r4                 
031c84: 8a031ba0       jb 0xfd06.0xa,0x031cbe         -> 031cbe
031c88: f3f894f6       movb RL4,0xf694               
031c8c: 47f89f00       cmpb RL4,#0x9f                
031c90: fd12           jmpr cc_ULE,0x031cb6           -> 031cb6
031c92: 3f2c           bset 0xfd58.0x3               
031c94: e7fa2000       movb RL5,#0x20                
031c98: 75fae8f5       orb 0xf5e8,RL5                
031c9c: c2f491ef       movbz r4,0xef91               
031ca0: 06f41c00       add r4,#0x1c                  
031ca4: c2f594f6       movbz r5,0xf694               
031ca8: 4054           cmp r5,r4                     
031caa: fd09           jmpr cc_ULE,0x031cbe           -> 031cbe
031cac: 8a0a0700       jb 0xfd14.0x0,0x031cbe         -> 031cbe
031cb0: af03           bset 0xfd06.0xa               
031cb2: cf16           bset 0xfd2c.0xc               
031cb4: 0d04           jmpr cc_UC,0x031cbe            -> 031cbe
031cb6: e7f81000       movb RL4,#0x10                
031cba: 75f8e8f5       orb 0xf5e8,RL4                
031cbe: e6fc6419       mov r12,#0x1964               
031cc2: c2fd94f6       movbz r13,0xf694              
031cc6: da03024b       calls 0x034b02                 -> 034b02
031cca: e6fc6619       mov r12,#0x1966               
031cce: da03b24b       calls 0x034bb2                 -> 034bb2
031cd2: f6f4eaef       mov 0xefea,r4                 
031cd6: e6fc7819       mov r12,#0x1978               
031cda: da03b24b       calls 0x034bb2                 -> 034bb2
031cde: f6f4ecef       mov 0xefec,r4                 
031ce2: e6fc9c19       mov r12,#0x199c               
031ce6: da03b24b       calls 0x034bb2                 -> 034bb2
031cea: f6f4eeef       mov 0xefee,r4                 
031cee: e6fc8a19       mov r12,#0x198a               
031cf2: da03b24b       calls 0x034bb2                 -> 034bb2
031cf6: f6f4f0ef       mov 0xeff0,r4                 
031cfa: ea00d061       jmpa cc_UC,0x0361d0            -> 0361d0
031d92: 6411da03       and 0x3da,0xfe22              
031d96: 6a4af7f8       band r7.0x8,0xfd94.0xf        
031d9a: 73e9e6fc       orb 0xffd2,0xfce6             
031d9e: 1e11           bclr 0xfd22.0x1               
031da0: c2fd94f6       movbz r13,0xf694              
031da4: da03ac48       calls 0x0348ac                 -> 0348ac
031da8: e6fc1c11       mov r12,#0x111c               
031dac: f2fdc8e8       mov r13,0xe8c8                
031db0: da039e49       calls 0x03499e                 -> 03499e
031db4: 8a030460       jb 0xfd06.0x6,0x031dc0         -> 031dc0
031db8: 8a0a0210       jb 0xfd14.0x1,0x031dc0         -> 031dc0
031dbc: 9a0a0800       jnb 0xfd14.0x0,0x031dd0        -> 031dd0
031dc0: f78e74e9       movb 0xe974,0xff1c            
031dc4: e6fc2011       mov r12,#0x1120               
031dc8: da036a4a       calls 0x034a6a                 -> 034a6a
031dcc: f7f875e9       movb 0xe975,RL4               
031dd0: 8a0d0450       jb 0xfd1a.0x5,0x031ddc         -> 031ddc
031dd4: 8a0a0210       jb 0xfd14.0x1,0x031ddc         -> 031ddc
031dd8: 9a0a0800       jnb 0xfd14.0x0,0x031dec        -> 031dec
031ddc: f78ea6f4       movb 0xf4a6,0xff1c            
031de0: e6fc8a0f       mov r12,#0xf8a                
031de4: da036a4a       calls 0x034a6a                 -> 034a6a
031de8: f7f8a7f4       movb 0xf4a7,RL4               
031dec: e6fc961d       mov r12,#0x1d96               
031df0: c2fd94f6       movbz r13,0xf694              
031df4: da03ac48       calls 0x0348ac                 -> 0348ac
031df8: e6fc981d       mov r12,#0x1d98               
031dfc: da03344a       calls 0x034a34                 -> 034a34
031e00: f7f8b2f4       movb 0xf4b2,RL4               
031e04: e6fc441a       mov r12,#0x1a44               
031e08: c2fd94f6       movbz r13,0xf694              
031e0c: da03024b       calls 0x034b02                 -> 034b02
031e10: e6fc461a       mov r12,#0x1a46               
031e14: da03b24b       calls 0x034bb2                 -> 034bb2
031e18: f6f480e9       mov 0xe980,r4                 
031e1c: e6fcf82e       mov r12,#0x2ef8               
031e20: c2fd94f6       movbz r13,0xf694              
031e24: da03ac48       calls 0x0348ac                 -> 0348ac
031e28: e6fcfa2e       mov r12,#0x2efa               
031e2c: da03504a       calls 0x034a50                 -> 034a50
031e30: f6f4d8f4       mov 0xf4d8,r4                 
031e34: e6fc7e04       mov r12,#0x47e                
031e38: c2fd94f6       movbz r13,0xf694              
031e3c: da03ac48       calls 0x0348ac                 -> 0348ac
031e40: e6fc8004       mov r12,#0x480                
031e44: da03344a       calls 0x034a34                 -> 034a34
031e48: f7f860e8       movb 0xe860,RL4               
031e4c: ea00d061       jmpa cc_UC,0x0361d0            -> 0361d0
0321f0: 49e1           cmpb RL7,#0x1                 
0321f2: 3d08           jmpr cc_NE,0x032204            -> 032204
0321f4: e6f918f0       mov r9,#0xf018                
0321f8: f3f811f7       movb RL4,0xf711               
0321fc: f7f898f5       movb 0xf598,RL4               
032200: f1c8           movb RL6,RL4                  
032202: 0d07           jmpr cc_UC,0x032212            -> 032212
032204: e6f9c4f0       mov r9,#0xf0c4                
032208: f3f812f7       movb RL4,0xf712               
03220c: f7f899f5       movb 0xf599,RL4               
032210: f1c8           movb RL6,RL4                  
032212: 43fc9003       cmpb RL6,0x390                
032216: fd04           jmpr cc_ULE,0x032220           -> 032220
032218: e118           movb RL4,#0x1                 
03221a: f7f80eea       movb 0xea0e,RL4               
03221e: 0d12           jmpr cc_UC,0x032244            -> 032244
032220: 43fc9103       cmpb RL6,0x391                
032224: 9d04           jmpr cc_NC,0x03222e            -> 03222e
032226: e128           movb RL4,#0x2                 
032228: f7f80eea       movb 0xea0e,RL4               
03222c: 0d0b           jmpr cc_UC,0x032244            -> 032244
03222e: e6fca031       mov r12,#0x31a0               
032232: c0cd           movbz r13,RL6                 
032234: da03ac48       calls 0x0348ac                 -> 0348ac
032238: e6fca231       mov r12,#0x31a2               
03223c: da03504a       calls 0x034a50                 -> 034a50
032240: c4497600       mov [r9+#0x76],r4             
032244: 49e1           cmpb RL7,#0x1                 
032246: 3d16           jmpr cc_NE,0x032274            -> 032274
032248: e6fc46ee       mov r12,#0xee46               
03224c: e6fd5cab       mov r13,#0xab5c               
032250: c2fe3702       movbz r14,0x237               
032254: c2ff6002       movbz r15,0x260               
032258: da025679       calls 0x027956                 -> 027956
03225c: 4a1c2865       bmov 0xfd50.0x5,0xfd38.0x6    
032260: c2f450ee       movbz r4,0xee50               
032264: 66f42000       and r4,#0x20                  
032268: 2d1a           jmpr cc_EQ,0x03229e            -> 03229e
03226a: e6f4fffd       mov r4,#0xfdff                
03226e: 64f4fcf5       and 0xf5fc,r4                 
032272: 0d15           jmpr cc_UC,0x03229e            -> 03229e
032274: e6fc52ee       mov r12,#0xee52               
032278: e6fd6cab       mov r13,#0xab6c               
03227c: c2fe3702       movbz r14,0x237               
032280: c2ff6002       movbz r15,0x260               
032284: da025679       calls 0x027956                 -> 027956
032288: 4a1c2866       bmov 0xfd50.0x6,0xfd38.0x6    
03228c: c2f45cee       movbz r4,0xee5c               
032290: 66f42000       and r4,#0x20                  
032294: 2d04           jmpr cc_EQ,0x03229e            -> 03229e
032296: e6f4fffb       mov r4,#0xfbff                
03229a: 64f4fcf5       and 0xf5fc,r4                 
03229e: 09e1           addb RL7,#0x1                 
0322a0: 43fe76f1       cmpb RL7,0xf176               
0322a4: fda5           jmpr cc_ULE,0x0321f0           -> 0321f0
0322a6: 9890           mov r9,[r0+]                  
0322a8: 9870           mov r7,[r0+]                  
0322aa: 9860           mov r6,[r0+]                  
0322ac: db00           rets                          
032386: e6f418f0       mov r4,#0xf018                
03238a: f6f460ef       mov 0xef60,r4                 
03238e: 0d04           jmpr cc_UC,0x032398            -> 032398
032398: f78f58ef       movb 0xef58,0xff1e            
03239c: 1e1e           bclr 0xfd3c.0x1               
03239e: e6fcae06       mov r12,#0x6ae                
0323a2: c2fd62ef       movbz r13,0xef62              
0323a6: da03ac48       calls 0x0348ac                 -> 0348ac
0323aa: e6fcb006       mov r12,#0x6b0                
0323ae: da03344a       calls 0x034a34                 -> 034a34
0323b2: f1c8           movb RL6,RL4                  
0323b4: e6fcb606       mov r12,#0x6b6                
0323b8: c2fd9dfc       movbz r13,0xfc9d              
0323bc: da03ac48       calls 0x0348ac                 -> 0348ac
0323c0: 9a031b50       jnb 0xfd06.0x5,0x0323fa        -> 0323fa
0323c4: e6fcb806       mov r12,#0x6b8                
0323c8: da03344a       calls 0x034a34                 -> 034a34
0323cc: 27f88000       subb RL4,#0x80                
0323d0: d08d           movbs r13,RL4                 
0323d2: f18c           movb RL4,RL6                  
0323d4: c08c           movbz r12,RL4                 
0323d6: da030e45       calls 0x03450e                 -> 03450e
0323da: 4982           cmpb RL4,#0x2                 
0323dc: 8d0e           jmpr cc_C,0x0323fa             -> 0323fa
0323de: e6fcb806       mov r12,#0x6b8                
0323e2: da03344a       calls 0x034a34                 -> 034a34
0323e6: 27f88000       subb RL4,#0x80                
0323ea: d08d           movbs r13,RL4                 
0323ec: f18c           movb RL4,RL6                  
0323ee: c08c           movbz r12,RL4                 
0323f0: da030e45       calls 0x03450e                 -> 03450e
0323f4: f7f8e6f6       movb 0xf6e6,RL4               
0323f8: 0d0a           jmpr cc_UC,0x03240e            -> 03240e
0323fa: e128           movb RL4,#0x2                 
0323fc: f7f8e6f6       movb 0xf6e6,RL4               
032400: 0d06           jmpr cc_UC,0x03240e            -> 03240e
03240e: 9860           mov r6,[r0+]                  
032410: db00           rets                          
03256a: f2f474f1       mov r4,0xf174                 
03256e: 4049           cmp r4,r9                     
032570: 2d3b           jmpr cc_EQ,0x0325e8            -> 0325e8
032572: e6fca429       mov r12,#0x29a4               
032576: f2fd54fc       mov r13,0xfc54                
03257a: da035049       calls 0x034950                 -> 034950
03257e: f2f474f1       mov r4,0xf174                 
032582: 4049           cmp r4,r9                     
032584: fd19           jmpr cc_ULE,0x0325b8           -> 0325b8
032586: e6fca629       mov r12,#0x29a6               
03258a: da03504a       calls 0x034a50                 -> 034a50
03258e: f0d4           mov r13,r4                    
032590: f0c9           mov r12,r9                    
032592: da038444       calls 0x034484                 -> 034484
032596: f084           mov r8,r4                     
032598: f2f474f1       mov r4,0xf174                 
03259c: 4048           cmp r4,r8                     
03259e: fd02           jmpr cc_ULE,0x0325a4           -> 0325a4
0325a0: f6f874f1       mov 0xf174,r8                 
0325a4: f2f474f1       mov r4,0xf174                 
0325a8: 46f400d0       cmp r4,#0xd000                
0325ac: fd1d           jmpr cc_ULE,0x0325e8           -> 0325e8
0325ae: e6f500d0       mov r5,#0xd000                
0325b2: f6f574f1       mov 0xf174,r5                 
0325b6: 0d18           jmpr cc_UC,0x0325e8            -> 0325e8
0325b8: e6fca629       mov r12,#0x29a6               
0325bc: da03504a       calls 0x034a50                 -> 034a50
0325c0: f0d4           mov r13,r4                    
0325c2: f0c9           mov r12,r9                    
0325c4: da03a444       calls 0x0344a4                 -> 0344a4
0325c8: f084           mov r8,r4                     
0325ca: f2f474f1       mov r4,0xf174                 
0325ce: 4048           cmp r4,r8                     
0325d0: 9d02           jmpr cc_NC,0x0325d6            -> 0325d6
0325d2: f6f874f1       mov 0xf174,r8                 
0325d6: f2f474f1       mov r4,0xf174                 
0325da: 46f40030       cmp r4,#0x3000                
0325de: 9d04           jmpr cc_NC,0x0325e8            -> 0325e8
0325e0: e6f50030       mov r5,#0x3000                
0325e4: f6f574f1       mov 0xf174,r5                 
0325e8: 9890           mov r9,[r0+]                  
0325ea: 9880           mov r8,[r0+]                  
0325ec: db00           rets                          
032e9a: 81d0           neg r13                       
032e9c: da02debe       calls 0x02bede                 -> 02bede
032ea0: f094           mov r9,r4                     
032ea2: f0c9           mov r12,r9                    
032ea4: da036866       calls 0x036668                 -> 036668
032ea8: f2fc5aef       mov r12,0xef5a                
032eac: f0d9           mov r13,r9                    
032eae: da02debe       calls 0x02bede                 -> 02bede
032eb2: f6f45aef       mov 0xef5a,r4                 
032eb6: f6f46eef       mov 0xef6e,r4                 
032eba: f2fc70ef       mov r12,0xef70                
032ebe: f2fd5cef       mov r13,0xef5c                
032ec2: da02debe       calls 0x02bede                 -> 02bede
032ec6: f6f470ef       mov 0xef70,r4                 
032eca: 42f4b000       cmp r4,0xb0                   
032ece: ed04           jmpr cc_UGT,0x032ed8           -> 032ed8
032ed0: 8140           neg r4                        
032ed2: 42f4b000       cmp r4,0xb0                   
032ed6: fd08           jmpr cc_ULE,0x032ee8           -> 032ee8
032ed8: df1d           bset 0xfd3a.0xd               
032eda: 8a1f0560       jb 0xfd3e.0x6,0x032ee8         -> 032ee8
032ede: 4e1f           bclr 0xfd3e.0x4               
032ee0: 5e1f           bclr 0xfd3e.0x5               
032ee2: 0d02           jmpr cc_UC,0x032ee8            -> 032ee8
032ee8: 9890           mov r9,[r0+]                  
032eea: 9880           mov r8,[r0+]                  
032eec: db00           rets                          
03440c: 98d0           mov r13,[r0+]                 
03440e: da036245       calls 0x034562                 -> 034562
034412: f074           mov r7,r4                     
034414: 4870           cmp r7,#0x0                   
034416: bd07           jmpr cc_SLE,0x034426           -> 034426
034418: f2fc9600       mov r12,0x96                  
03441c: f0d7           mov r13,r7                    
03441e: da030447       calls 0x034704                 -> 034704
034422: f074           mov r7,r4                     
034424: 0d01           jmpr cc_UC,0x034428            -> 034428
034426: e007           mov r7,#0x0                   
034428: f2fc02db       mov r12,0xdb02                
03442c: f0d7           mov r13,r7                    
03442e: f2fee800       mov r14,0xe8                  
034432: da03f24b       calls 0x034bf2                 -> 034bf2
034436: f6f4fcf4       mov 0xf4fc,r4                 
03443a: f4a9f2ab       movb RL5,[r9+#0xabf2]         
03443e: c0a5           movbz r5,RL5                  
034440: 5c15           shl r5,#0x1                   
034442: c445f0f4       mov [r5+#0xf4f0],r4           
034446: f2fce0f4       mov r12,0xf4e0                
03444a: f2fde2f4       mov r13,0xf4e2                
03444e: da036245       calls 0x034562                 -> 034562
034452: f0c4           mov r12,r4                    
034454: f2fdfcf4       mov r13,0xf4fc                
034458: da036245       calls 0x034562                 -> 034562
03445c: f4a9f2ab       movb RL5,[r9+#0xabf2]         
034460: c0a5           movbz r5,RL5                  
034462: 5c15           shl r5,#0x1                   
034464: c445e4f4       mov [r5+#0xf4e4],r4           
034468: c2f45af5       movbz r4,0xf55a               
03446c: f4a9f2ab       movb RL5,[r9+#0xabf2]         
034470: c0a5           movbz r5,RL5                  
034472: f465d4ab       movb RL3,[r5+#0xabd4]         
034476: c065           movbz r5,RL3                  
034478: 6045           and r4,r5                     
03447a: 2d2b           jmpr cc_EQ,0x0344d2            -> 0344d2
03447c: e014           mov r4,#0x1                   
03447e: 04f4fef4       add 0xf4fe,r4                 
034482: f2f4fef4       mov r4,0xf4fe                 
034486: 3d03           jmpr cc_NE,0x03448e            -> 03448e
034488: e015           mov r5,#0x1                   
03448a: 04f500f5       add 0xf500,r5                 
03448e: f489f2ab       movb RL4,[r9+#0xabf2]         
034492: c084           movbz r4,RL4                  
034494: 5c14           shl r4,#0x1                   
034496: d454ccf4       mov r5,[r4+#0xf4cc]           
03449a: f489f2ab       movb RL4,[r9+#0xabf2]         
03449e: c084           movbz r4,RL4                  
0344a0: 5c14           shl r4,#0x1                   
0344a2: d424e4f4       mov r2,[r4+#0xf4e4]           
0344a6: 4025           cmp r2,r5                     
0344a8: dd0f           jmpr cc_SGE,0x0344c8           -> 0344c8
0344aa: f489f2ab       movb RL4,[r9+#0xabf2]         
0344ae: c084           movbz r4,RL4                  
0344b0: 5c14           shl r4,#0x1                   
0344b2: e6f502f5       mov r5,#0xf502                
0344b6: 0054           add r5,r4                     
0344b8: a845           mov r4,[r5]                   
0344ba: 0841           add r4,#0x1                   
0344bc: b845           mov [r5],r4                   
0344be: f489e0ab       movb RL4,[r9+#0xabe0]         
0344c2: 75f85bf5       orb 0xf55b,RL4                
0344c6: 0d09           jmpr cc_UC,0x0344da            -> 0344da
0344c8: f489e6ab       movb RL4,[r9+#0xabe6]         
0344cc: 65f85bf5       andb 0xf55b,RL4               
0344d0: 0d04           jmpr cc_UC,0x0344da            -> 0344da
0344d2: f489e6ab       movb RL4,[r9+#0xabe6]         
0344d6: 65f85bf5       andb 0xf55b,RL4               
0344da: 4890           cmp r9,#0x0                   
0344dc: ea30d006       jmpa cc_NE,0x0306d0            -> 0306d0
0344e0: 0e29           bclr 0xfd52.0x0               
0344e2: 9a290250       jnb 0xfd52.0x5,0x0344ea        -> 0344ea
0344e6: ea009e06       jmpa cc_UC,0x03069e            -> 03069e
0344ea: 8a0a0250       jb 0xfd14.0x5,0x0344f2         -> 0344f2
0344ee: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
0344f2: f3f85cf5       movb RL4,0xf55c               
0344f6: 47f83f00       cmpb RL4,#0x3f                
0344fa: ea30ce06       jmpa cc_NE,0x0306ce            -> 0306ce
0344fe: 9a290280       jnb 0xfd52.0x8,0x034506        -> 034506
034502: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
034506: 9a180220       jnb 0xfd30.0x2,0x03450e        -> 03450e
03450a: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
03450e: 9a180230       jnb 0xfd30.0x3,0x034516        -> 034516
034512: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
034516: 9a180210       jnb 0xfd30.0x1,0x03451e        -> 03451e
03451a: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
03451e: 9a1802f0       jnb 0xfd30.0xf,0x034526        -> 034526
034522: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
034526: 9a260260       jnb 0xfd4c.0x6,0x03452e        -> 03452e
03452a: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
03452e: 9a260230       jnb 0xfd4c.0x3,0x034536        -> 034536
034532: ea00ce06       jmpa cc_UC,0x0306ce            -> 0306ce
034536: f2f432fd       mov r4,0xfd32                 
03453a: 66f43f00       and r4,#0x3f                  
03453e: ea30ce06       jmpa cc_NE,0x0306ce            -> 0306ce
034542: f3f83cfc       movb RL4,0xfc3c               
034546: 43f8c502       cmpb RL4,0x2c5                
03454a: eaf0ce06       jmpa cc_ULE,0x0306ce           -> 0306ce
03454e: 43f8bc02       cmpb RL4,0x2bc                
034552: ea90ce06       jmpa cc_NC,0x0306ce            -> 0306ce
034556: f3fadef4       movb RL5,0xf4de               
03455a: ea30ce06       jmpa cc_NE,0x0306ce            -> 0306ce
03455e: f2fc4efc       mov r12,0xfc4e                
034562: f2fd42fc       mov r13,0xfc42                
034566: da03d644       calls 0x0344d6                 -> 0344d6
03456a: f074           mov r7,r4                     
03456c: 4870           cmp r7,#0x0                   
03456e: cd03           jmpr cc_SLT,0x034576           -> 034576
034570: 42f79000       cmp r7,0x90                   
034574: 8d09           jmpr cc_C,0x034588             -> 034588
034576: 4870           cmp r7,#0x0                   
034578: ead0ce06       jmpa cc_SGE,0x0306ce           -> 0306ce
03457c: f2f49000       mov r4,0x90                   
034580: 8140           neg r4                        
034582: 4074           cmp r7,r4                     
034584: eab0ce06       jmpa cc_SLE,0x0306ce           -> 0306ce
034588: 0f29           bset 0xfd52.0x0               
03458a: 6e29           bclr 0xfd52.0x6               
03458c: f087           mov r8,r7                     
03458e: e064           mov r4,#0x6                   
034590: f6f80efe       mov 0xfe0e,r8                 
034594: 4b44           div r4                        
034596: f2f80efe       mov r8,0xfe0e                 
03459a: e11c           movb RL6,#0x1                 
03459c: c0c4           movbz r4,RL6                  
03459e: 5c14           shl r4,#0x1                   
0345a0: d4c442fc       mov r12,[r4+#0xfc42]          
0345a4: f2fd42fc       mov r13,0xfc42                
0345a8: da03d644       calls 0x0344d6                 -> 0344d6
0345ac: f0c4           mov r12,r4                    
0345ae: c0cd           movbz r13,RL6                 
0345b0: 0bd8           mul r13,r8                    
0345b2: f2fd0efe       mov r13,0xfe0e                
0345b6: 81d0           neg r13                       
0345b8: da036245       calls 0x034562                 -> 034562
0345bc: f074           mov r7,r4                     
0345be: 4870           cmp r7,#0x0                   
0345c0: cd0a           jmpr cc_SLT,0x0345d6           -> 0345d6
0345c2: 46f7ff07       cmp r7,#0x7ff                 
0345c6: dd04           jmpr cc_SGE,0x0345d0           -> 0345d0
0345c8: f047           mov r4,r7                     
0345ca: 5c64           shl r4,#0x6                   
0345cc: f074           mov r7,r4                     
0345ce: 0d0c           jmpr cc_UC,0x0345e8            -> 0345e8
0345d0: e6f7ff7f       mov r7,#0x7fff                
0345d4: 0d09           jmpr cc_UC,0x0345e8            -> 0345e8
0345d6: 46f701f8       cmp r7,#0xf801                
0345da: dd03           jmpr cc_SGE,0x0345e2           -> 0345e2
0345dc: e6f70080       mov r7,#0x8000                
0345e0: 0d03           jmpr cc_UC,0x0345e8            -> 0345e8
0345e2: f047           mov r4,r7                     
0345e4: 5c64           shl r4,#0x6                   
0345e6: f074           mov r7,r4                     
0345e8: f0c7           mov r12,r7                    
0345ea: f2fd42fc       mov r13,0xfc42                
0345ee: da037247       calls 0x034772                 -> 034772
0345f2: f074           mov r7,r4                     
0345f4: c0c4           movbz r4,RL6                  
0345f6: 5c14           shl r4,#0x1                   
0345f8: d4c40ef5       mov r12,[r4+#0xf50e]          
0345fc: 06fc0080       add r12,#0x8000               
034600: e6fd0080       mov r13,#0x8000               
034604: 00d7           add r13,r7                    
034606: f2fe5a01       mov r14,0x15a                 
03460a: da03f24b       calls 0x034bf2                 -> 034bf2
03460e: 06f40080       add r4,#0x8000                
034612: f074           mov r7,r4                     
034614: f2f4a800       mov r4,0xa8                   
034618: 5c64           shl r4,#0x6                   
03461a: 4074           cmp r7,r4                     
03461c: bd08           jmpr cc_SLE,0x03462e           -> 03462e
03461e: f2f7a800       mov r7,0xa8                   
034622: 5c67           shl r7,#0x6                   
034624: e118           movb RL4,#0x1                 
034626: f7f80eea       movb 0xea0e,RL4               
03462a: 6f29           bset 0xfd52.0x6               
03462c: 0d0e           jmpr cc_UC,0x03464a            -> 03464a
03462e: f2f4a800       mov r4,0xa8                   
034632: 5c64           shl r4,#0x6                   
034634: 8140           neg r4                        
034636: 4074           cmp r7,r4                     
034638: dd08           jmpr cc_SGE,0x03464a           -> 03464a
03463a: f2f7a800       mov r7,0xa8                   
03463e: 5c67           shl r7,#0x6                   
034640: 8170           neg r7                        
034642: e118           movb RL4,#0x1                 
034644: f7f80eea       movb 0xea0e,RL4               
034648: 6f29           bset 0xfd52.0x6               
03464a: c0c4           movbz r4,RL6                  
03464c: 5c14           shl r4,#0x1                   
03464e: c4740ef5       mov [r4+#0xf50e],r7           
034652: 09c1           addb RL6,#0x1                 
034654: 49c6           cmpb RL6,#0x6                 
034656: 8da2           jmpr cc_C,0x03459c             -> 03459c
034658: 8a290b10       jb 0xfd52.0x1,0x034672         -> 034672
03465c: f2fcdaf4       mov r12,0xf4da                
034660: f2fddcf4       mov r13,0xf4dc                
034664: da03a444       calls 0x0344a4                 -> 0344a4
034668: f6f4daf4       mov 0xf4da,r4                 
03466c: 4840           cmp r4,#0x0                   
03466e: 3d01           jmpr cc_NE,0x034672            -> 034672
034670: 1f29           bset 0xfd52.0x1               
034672: e6fc3eed       mov r12,#0xed3e               
034676: e6fdfca9       mov r13,#0xa9fc               
03467a: c2fe3902       movbz r14,0x239               
03467e: c2ff6202       movbz r15,0x262               
034682: da025679       calls 0x027956                 -> 027956
034686: 4a1c266f       bmov 0xfd4c.0xf,0xfd38.0x6    
03468a: c2f448ed       movbz r4,0xed48               
03468e: 66f42000       and r4,#0x20                  
034692: 2d1d           jmpr cc_EQ,0x0346ce            -> 0346ce
034694: e6f4f7ff       mov r4,#0xfff7                
034698: 64f4faf5       and 0xf5fa,r4                 
03469c: 0d18           jmpr cc_UC,0x0346ce            -> 0346ce
0346b8: 4a1c266f       bmov 0xfd4c.0xf,0xfd38.0x6    
0346bc: c2f448ed       movbz r4,0xed48               
0346c0: 66f42000       and r4,#0x20                  
0346c4: 2d04           jmpr cc_EQ,0x0346ce            -> 0346ce
0346c6: e6f4f7ff       mov r4,#0xfff7                
0346ca: 64f4faf5       and 0xf5fa,r4                 
0346ce: 5e29           bclr 0xfd52.0x5               
0346d0: f2f4bef4       mov r4,0xf4be                 
0346d4: f059           mov r5,r9                     
0346d6: 5c15           shl r5,#0x1                   
0346d8: c445c0f4       mov [r5+#0xf4c0],r4           
0346dc: 9890           mov r9,[r0+]                  
0346de: 9880           mov r8,[r0+]                  
0346e0: 9870           mov r7,[r0+]                  
0346e2: 9860           mov r6,[r0+]                  
0346e4: db00           rets                          
034704: 41a8           cmpb RL5,RL4                  
034706: 9d02           jmpr cc_NC,0x03470c            -> 03470c
034708: a40c3cfc       movb [r12],0xfc3c             
03470c: f48c0300       movb RL4,[r12+#0x3]           
034710: f3fae5e8       movb RL5,0xe8e5               
034714: 41a8           cmpb RL5,RL4                  
034716: fd03           jmpr cc_ULE,0x03471e           -> 03471e
034718: e4ac0300       movb [r12+#0x3],RL5           
03471c: db00           rets                          
03471e: f48c0100       movb RL4,[r12+#0x1]           
034722: f3fae5e8       movb RL5,0xe8e5               
034726: 41a8           cmpb RL5,RL4                  
034728: 9d0e           jmpr cc_NC,0x034746            -> 034746
03472a: e4ac0100       movb [r12+#0x1],RL5           
03472e: db00           rets                          
034744: 0100           addb RL0,RL0                  
034746: db00           rets                          
034772: fc48           pop 0xfe90                    
034774: e6fc0232       mov r12,#0x3202               
034778: da036a4a       calls 0x034a6a                 -> 034a6a
03477c: c089           movbz r9,RL4                  
03477e: 0d01           jmpr cc_UC,0x034782            -> 034782
034782: c2f440f5       movbz r4,0xf540               
034786: c2f59ffc       movbz r5,0xfc9f               
03478a: f465d4ab       movb RL3,[r5+#0xabd4]         
03478e: c065           movbz r5,RL3                  
034790: 6045           and r4,r5                     
034792: 2d1b           jmpr cc_EQ,0x0347ca            -> 0347ca
034794: c2f440f5       movbz r4,0xf540               
034798: 66f43f00       and r4,#0x3f                  
03479c: 46f43f00       cmp r4,#0x3f                  
0347a0: 2d14           jmpr cc_EQ,0x0347ca            -> 0347ca
0347a2: 8a1912d0       jb 0xfd32.0xd,0x0347ca         -> 0347ca
0347a6: e6fc2832       mov r12,#0x3228               
0347aa: c2fd3cfc       movbz r13,0xfc3c              
0347ae: da03ac48       calls 0x0348ac                 -> 0348ac
0347b2: e6fc2632       mov r12,#0x3226               
0347b6: c2fde5e8       movbz r13,0xe8e5              
0347ba: da03fc48       calls 0x0348fc                 -> 0348fc
0347be: e6fc2a32       mov r12,#0x322a               
0347c2: da036a4a       calls 0x034a6a                 -> 034a6a
0347c6: c084           movbz r4,RL4                  
0347c8: 0094           add r9,r4                     
0347ca: c2f441f5       movbz r4,0xf541               
0347ce: c2f59ffc       movbz r5,0xfc9f               
0347d2: f465d4ab       movb RL3,[r5+#0xabd4]         
0347d6: c065           movbz r5,RL3                  
0347d8: 6045           and r4,r5                     
0347da: 2d02           jmpr cc_EQ,0x0347e0            -> 0347e0
0347dc: 06f96400       add r9,#0x64                  
0347e0: f6f942f5       mov 0xf542,r9                 
0347e4: f049           mov r4,r9                     
0347e6: 46f46400       cmp r4,#0x64                  
0347ea: 8d2f           jmpr cc_C,0x03484a             -> 03484a
0347ec: e6fcd831       mov r12,#0x31d8               
0347f0: c2fd3cfc       movbz r13,0xfc3c              
0347f4: da03ac48       calls 0x0348ac                 -> 0348ac
0347f8: e6fcd631       mov r12,#0x31d6               
0347fc: c2fde5e8       movbz r13,0xe8e5              
034800: da03fc48       calls 0x0348fc                 -> 0348fc
034804: e6fcda31       mov r12,#0x31da               
034808: da036a4a       calls 0x034a6a                 -> 034a6a
03480c: c084           movbz r4,RL4                  
03480e: c2f59ffc       movbz r5,0xfc9f               
034812: 5c15           shl r5,#0x1                   
034814: e6f21cf5       mov r2,#0xf51c                
034818: 0025           add r2,r5                     
03481a: a852           mov r5,[r2]                   
03481c: 0054           add r5,r4                     
03481e: b852           mov [r2],r5                   
034820: c2f49ffc       movbz r4,0xfc9f               
034824: 5c14           shl r4,#0x1                   
034826: e6f528f5       mov r5,#0xf528                
03482a: 0054           add r5,r4                     
03482c: a845           mov r4,[r5]                   
03482e: 0841           add r4,#0x1                   
034830: b845           mov [r5],r4                   
034832: e014           mov r4,#0x1                   
034834: 04f434f5       add 0xf534,r4                 
034838: e6fc4ef5       mov r12,#0xf54e               
03483c: da03e606       calls 0x0306e6                 -> 0306e6
034840: e6fc52f5       mov r12,#0xf552               
034844: da03e606       calls 0x0306e6                 -> 0306e6
034848: 0d04           jmpr cc_UC,0x034852            -> 034852
03484a: e6fc56f5       mov r12,#0xf556               
03484e: da03e606       calls 0x0306e6                 -> 0306e6
034852: e014           mov r4,#0x1                   
034854: 04f436f5       add 0xf536,r4                 
034858: f2f436f5       mov r4,0xf536                 
03485c: 46f45802       cmp r4,#0x258                 
034860: ea30d00c       jmpa cc_NE,0x030cd0            -> 030cd0
034864: 2e2c           bclr 0xfd58.0x2               
034866: f2f91cf5       mov r9,0xf51c                 
03486a: 02f91ef5       add r9,0xf51e                 
03486e: 02f920f5       add r9,0xf520                 
034872: 02f922f5       add r9,0xf522                 
034876: 02f924f5       add r9,0xf524                 
03487a: 02f926f5       add r9,0xf526                 
03487e: 42f91201       cmp r9,0x112                  
034882: fd55           jmpr cc_ULE,0x03492e           -> 03492e
034884: e6fc1cf5       mov r12,#0xf51c               
034888: e06d           mov r13,#0x6                  
03488a: da038048       calls 0x034880                 -> 034880
03488e: c089           movbz r9,RL4                  
034890: af29           bset 0xfd52.0xa               
034892: bf29           bset 0xfd52.0xb               
034894: c2f444f5       movbz r4,0xf544               
034898: f4a9d4ab       movb RL5,[r9+#0xabd4]         
03489c: c0a5           movbz r5,RL5                  
03489e: 6045           and r4,r5                     
0348a0: 3d44           jmpr cc_NE,0x03492a            -> 03492a
0348a2: f3f81af5       movb RL4,0xf51a               
0348a6: 43f8f501       cmpb RL4,0x1f5                
0348aa: 9d3d           jmpr cc_NC,0x034926            -> 034926
0348ac: f4a9d4ab       movb RL5,[r9+#0xabd4]         
0348b0: 75fa44f5       orb 0xf544,RL5                
0348b4: f3fa44f5       movb RL5,0xf544               
0348b8: 73faf9e9       orb RL5,0xe9f9                
0348bc: f7fa35fc       movb 0xfc35,RL5               
0348c0: 0981           addb RL4,#0x1                 
0348c2: f7f81af5       movb 0xf51a,RL4               
0348c6: f3f6f701       movb RL3,0x1f7                
0348ca: f7f61bf5       movb 0xf51b,RL3               
0348ce: c2f4daea       movbz r4,0xeada               
0348d2: 66f44200       and r4,#0x42                  
0348d6: 46f44200       cmp r4,#0x42                  
0348da: 2d23           jmpr cc_EQ,0x034922            -> 034922
0348dc: c2f4e6ea       movbz r4,0xeae6               
0348e0: 66f44200       and r4,#0x42                  
0348e4: 46f44200       cmp r4,#0x42                  
0348e8: 2d1c           jmpr cc_EQ,0x034922            -> 034922
0348ea: c2f4f2ea       movbz r4,0xeaf2               
0348ee: 66f44200       and r4,#0x42                  
0348f2: 46f44200       cmp r4,#0x42                  
0348f6: 2d15           jmpr cc_EQ,0x034922            -> 034922
0348f8: c2f4feea       movbz r4,0xeafe               
0348fc: 66f44200       and r4,#0x42                  
034900: 46f44200       cmp r4,#0x42                  
034904: 2d0e           jmpr cc_EQ,0x034922            -> 034922
034906: c2f40aeb       movbz r4,0xeb0a               
03490a: 66f44200       and r4,#0x42                  
03490e: 46f44200       cmp r4,#0x42                  
034912: 2d07           jmpr cc_EQ,0x034922            -> 034922
034914: c2f416eb       movbz r4,0xeb16               
034918: 66f44200       and r4,#0x42                  
03491c: 46f44200       cmp r4,#0x42                  
034920: 3d08           jmpr cc_NE,0x034932            -> 034932
034922: 2f2c           bset 0xfd58.0x2               
034924: 0d06           jmpr cc_UC,0x034932            -> 034932
034926: 2f2c           bset 0xfd58.0x2               
034928: 0d04           jmpr cc_UC,0x034932            -> 034932
03492a: 2f2c           bset 0xfd58.0x2               
03492c: 0d02           jmpr cc_UC,0x034932            -> 034932
03492e: ae29           bclr 0xfd52.0xa               
034930: be29           bclr 0xfd52.0xb               
034932: f3f850f5       movb RL4,0xf550               
034936: f7f862e8       movb 0xe862,RL4               
03493a: f3fa4ff5       movb RL5,0xf54f               
03493e: f7fa63e8       movb 0xe863,RL5               
034942: f3f651f5       movb RL3,0xf551               
034946: f7f664e8       movb 0xe864,RL3               
03494a: 8a2902a0       jb 0xfd52.0xa,0x034952         -> 034952
03494e: ea00a80b       jmpa cc_UC,0x030ba8            -> 030ba8
034952: f2f41cf5       mov r4,0xf51c                 
034956: 42f46e00       cmp r4,0x6e                   
03495a: ed02           jmpr cc_UGT,0x034960           -> 034960
03495c: 4890           cmp r9,#0x0                   
03495e: 3d12           jmpr cc_NE,0x034984            -> 034984
034960: e6fc4400       mov r12,#0x44                 
034964: da03d41b       calls 0x031bd4                 -> 031bd4
034968: c2f454ed       movbz r4,0xed54               
03496c: 66f40800       and r4,#0x8                   
034970: 2d06           jmpr cc_EQ,0x03497e            -> 03497e
034972: da03921d       calls 0x031d92                 -> 031d92
034976: f3f8d4ab       movb RL4,0xabd4               
03497a: 75f845f5       orb 0xf545,RL4                
03497e: e118           movb RL4,#0x1                 
034980: f7f80eea       movb 0xea0e,RL4               
034984: e6fc4aed       mov r12,#0xed4a               
034988: e6fd0caa       mov r13,#0xaa0c               
03498c: c2fe5502       movbz r14,0x255               
034990: c2ff5502       movbz r15,0x255               
034994: da025679       calls 0x027956                 -> 027956
034998: 4a1c2760       bmov 0xfd4e.0x0,0xfd38.0x6    
03499c: c2f454ed       movbz r4,0xed54               
0349a0: 66f42000       and r4,#0x20                  
0349a4: 2d04           jmpr cc_EQ,0x0349ae            -> 0349ae
0349a6: e6f4efff       mov r4,#0xffef                
0349aa: 64f4faf5       and 0xf5fa,r4                 
0349ae: f2f41ef5       mov r4,0xf51e                 
0349b2: 42f46e00       cmp r4,0x6e                   
0349b6: ed02           jmpr cc_UGT,0x0349bc           -> 0349bc
0349b8: 4891           cmp r9,#0x1                   
0349ba: 3d12           jmpr cc_NE,0x0349e0            -> 0349e0
0349bc: e6fc4500       mov r12,#0x45                 
0349c0: da03d41b       calls 0x031bd4                 -> 031bd4
0349c4: c2f460ed       movbz r4,0xed60               
0349c8: 66f40800       and r4,#0x8                   
0349cc: 2d06           jmpr cc_EQ,0x0349da            -> 0349da
0349ce: da03921d       calls 0x031d92                 -> 031d92
0349d2: f3f8d5ab       movb RL4,0xabd5               
0349d6: 75f845f5       orb 0xf545,RL4                
0349da: e118           movb RL4,#0x1                 
0349dc: f7f80eea       movb 0xea0e,RL4               
0349e0: e6fc56ed       mov r12,#0xed56               
0349e4: e6fd1caa       mov r13,#0xaa1c               
0349e8: c2fe5502       movbz r14,0x255               
0349ec: c2ff5502       movbz r15,0x255               
0349f0: da025679       calls 0x027956                 -> 027956
0349f4: 4a1c2761       bmov 0xfd4e.0x1,0xfd38.0x6    
0349f8: c2f460ed       movbz r4,0xed60               
0349fc: 66f42000       and r4,#0x20                  
034a00: 2d04           jmpr cc_EQ,0x034a0a            -> 034a0a
034a02: e6f4dfff       mov r4,#0xffdf                
034a06: 64f4faf5       and 0xf5fa,r4                 
034a0a: f2f420f5       mov r4,0xf520                 
034a0e: 42f46e00       cmp r4,0x6e                   
034a12: ed02           jmpr cc_UGT,0x034a18           -> 034a18
034a14: 4892           cmp r9,#0x2                   
034a16: 3d12           jmpr cc_NE,0x034a3c            -> 034a3c
034a18: e6fc4600       mov r12,#0x46                 
034a1c: da03d41b       calls 0x031bd4                 -> 031bd4
034a20: c2f46ced       movbz r4,0xed6c               
034a24: 66f40800       and r4,#0x8                   
034a28: 2d06           jmpr cc_EQ,0x034a36            -> 034a36
034a2a: da03921d       calls 0x031d92                 -> 031d92
034a2e: f3f8d6ab       movb RL4,0xabd6               
034a32: 75f845f5       orb 0xf545,RL4                
034a36: e118           movb RL4,#0x1                 
034a38: f7f80eea       movb 0xea0e,RL4               
034a3c: e6fc62ed       mov r12,#0xed62               
034a40: e6fd2caa       mov r13,#0xaa2c               
034a44: c2fe5502       movbz r14,0x255               
034a48: c2ff5502       movbz r15,0x255               
034a4c: da025679       calls 0x027956                 -> 027956
034a50: 4a1c2762       bmov 0xfd4e.0x2,0xfd38.0x6    
034a54: c2f46ced       movbz r4,0xed6c               
034a58: 66f42000       and r4,#0x20                  
034a5c: 2d04           jmpr cc_EQ,0x034a66            -> 034a66
034a5e: e6f4bfff       mov r4,#0xffbf                
034a62: 64f4faf5       and 0xf5fa,r4                 
034a66: f2f422f5       mov r4,0xf522                 
034a6a: 42f46e00       cmp r4,0x6e                   
034a6e: ed02           jmpr cc_UGT,0x034a74           -> 034a74
034a70: 4893           cmp r9,#0x3                   
034a72: 3d12           jmpr cc_NE,0x034a98            -> 034a98
034a74: e6fc4700       mov r12,#0x47                 
034a78: da03d41b       calls 0x031bd4                 -> 031bd4
034a7c: c2f478ed       movbz r4,0xed78               
034a80: 66f40800       and r4,#0x8                   
034a84: 2d06           jmpr cc_EQ,0x034a92            -> 034a92
034a86: da03921d       calls 0x031d92                 -> 031d92
034a8a: f3f8d7ab       movb RL4,0xabd7               
034a8e: 75f845f5       orb 0xf545,RL4                
034a92: e118           movb RL4,#0x1                 
034a94: f7f80eea       movb 0xea0e,RL4               
034a98: e6fc6eed       mov r12,#0xed6e               
034a9c: e6fd3caa       mov r13,#0xaa3c               
034aa0: c2fe5502       movbz r14,0x255               
034aa4: c2ff5502       movbz r15,0x255               
034aa8: da025679       calls 0x027956                 -> 027956
034aac: 4a1c2763       bmov 0xfd4e.0x3,0xfd38.0x6    
034ab0: c2f478ed       movbz r4,0xed78               
034ab4: 66f42000       and r4,#0x20                  
034ab8: 2d04           jmpr cc_EQ,0x034ac2            -> 034ac2
034aba: e6f47fff       mov r4,#0xff7f                
034abe: 64f4faf5       and 0xf5fa,r4                 
034ac2: f2f424f5       mov r4,0xf524                 
034ac6: 42f46e00       cmp r4,0x6e                   
034aca: ed02           jmpr cc_UGT,0x034ad0           -> 034ad0
034acc: 4894           cmp r9,#0x4                   
034ace: 3d12           jmpr cc_NE,0x034af4            -> 034af4
034ad0: e6fc4800       mov r12,#0x48                 
034ad4: da03d41b       calls 0x031bd4                 -> 031bd4
034ad8: c2f484ed       movbz r4,0xed84               
034adc: 66f40800       and r4,#0x8                   
034ae0: 2d06           jmpr cc_EQ,0x034aee            -> 034aee
034ae2: da03921d       calls 0x031d92                 -> 031d92
034ae6: f3f8d8ab       movb RL4,0xabd8               
034aea: 75f845f5       orb 0xf545,RL4                
034aee: e118           movb RL4,#0x1                 
034af0: f7f80eea       movb 0xea0e,RL4               
034af4: e6fc7aed       mov r12,#0xed7a               
034af8: e6fd4caa       mov r13,#0xaa4c               
034afc: c2fe5502       movbz r14,0x255               
034b00: c2ff5502       movbz r15,0x255               
034b04: da025679       calls 0x027956                 -> 027956
034b08: 4a1c2764       bmov 0xfd4e.0x4,0xfd38.0x6    
034b0c: c2f484ed       movbz r4,0xed84               
034b10: 66f42000       and r4,#0x20                  
034b14: 2d04           jmpr cc_EQ,0x034b1e            -> 034b1e
034b16: e6f4fffe       mov r4,#0xfeff                
034b1a: 64f4faf5       and 0xf5fa,r4                 
034b1e: f2f426f5       mov r4,0xf526                 
034b22: 42f46e00       cmp r4,0x6e                   
034b26: ed02           jmpr cc_UGT,0x034b2c           -> 034b2c
034b28: 4895           cmp r9,#0x5                   
034b2a: 3d12           jmpr cc_NE,0x034b50            -> 034b50
034b2c: e6fc4900       mov r12,#0x49                 
034b30: da03d41b       calls 0x031bd4                 -> 031bd4
034b34: c2f490ed       movbz r4,0xed90               
034b38: 66f40800       and r4,#0x8                   
034b3c: 2d06           jmpr cc_EQ,0x034b4a            -> 034b4a
034b3e: da03921d       calls 0x031d92                 -> 031d92
034b42: f3f8d9ab       movb RL4,0xabd9               
034b46: 75f845f5       orb 0xf545,RL4                
034b4a: e118           movb RL4,#0x1                 
034b4c: f7f80eea       movb 0xea0e,RL4               
034b50: e6fc86ed       mov r12,#0xed86               
034b54: e6fd5caa       mov r13,#0xaa5c               
034b58: c2fe5502       movbz r14,0x255               
034b5c: c2ff5502       movbz r15,0x255               
034b60: da025679       calls 0x027956                 -> 027956
034b64: 4a1c2765       bmov 0xfd4e.0x5,0xfd38.0x6    
034b68: c2f490ed       movbz r4,0xed90               
034b6c: 66f42000       and r4,#0x20                  
034b70: 2d04           jmpr cc_EQ,0x034b7a            -> 034b7a
034b72: e6f4fffd       mov r4,#0xfdff                
034b76: 64f4faf5       and 0xf5fa,r4                 
034b7a: e009           mov r9,#0x0                   
034b7c: f489d4ab       movb RL4,[r9+#0xabd4]         
034b80: 63f845f5       andb RL4,0xf545               
034b84: 2d0a           jmpr cc_EQ,0x034b9a            -> 034b9a
034b86: f489daab       movb RL4,[r9+#0xabda]         
034b8a: 63f845f5       andb RL4,0xf545               
034b8e: e6f547f5       mov r5,#0xf547                
034b92: 0059           add r5,r9                     
034b94: a965           movb RL3,[r5]                 
034b96: 7168           orb RL3,RL4                   
034b98: b965           movb [r5],RL3                 
034b9a: 0891           add r9,#0x1                   
034b9c: 4896           cmp r9,#0x6                   
034b9e: 8dee           jmpr cc_C,0x034b7c             -> 034b7c
034ba0: f78e45f5       movb 0xf545,0xff1c            
034ba4: ea00a40c       jmpa cc_UC,0x030ca4            -> 030ca4
034bb2: 5502c2ff       xorb 0xffc2,0xfe04            
034bb6: 5502da02       xorb 0x2da,0xfe04             
034bba: 56794a1c       xor 0xfef2,#0x1c4a            
034bbe: 2760c2f4       subb 0xfec0,#0xc2             
034bc2: 54ed66f4       xor 0xf466,0xffda             
034bc6: 2000           sub r0,r0                     
034bc8: 2d04           jmpr cc_EQ,0x034bd2            -> 034bd2
034bca: e6f4efff       mov r4,#0xffef                
034bce: 64f4faf5       and 0xf5fa,r4                 
034bd2: e6fc56ed       mov r12,#0xed56               
034bd6: e6fd1caa       mov r13,#0xaa1c               
034bda: c2fe5502       movbz r14,0x255               
034bde: c2ff5502       movbz r15,0x255               
034be2: da025679       calls 0x027956                 -> 027956
034be6: 4a1c2761       bmov 0xfd4e.0x1,0xfd38.0x6    
034bea: c2f460ed       movbz r4,0xed60               
034bee: 66f42000       and r4,#0x20                  
034bf2: 2d04           jmpr cc_EQ,0x034bfc            -> 034bfc
034bf4: e6f4dfff       mov r4,#0xffdf                
034bf8: 64f4faf5       and 0xf5fa,r4                 
034bfc: e6fc62ed       mov r12,#0xed62               
034c00: e6fd2caa       mov r13,#0xaa2c               
034c04: c2fe5502       movbz r14,0x255               
034c08: c2ff5502       movbz r15,0x255               
034c0c: da025679       calls 0x027956                 -> 027956
034c10: 4a1c2762       bmov 0xfd4e.0x2,0xfd38.0x6    
034c14: c2f46ced       movbz r4,0xed6c               
034c18: 66f42000       and r4,#0x20                  
034c1c: 2d04           jmpr cc_EQ,0x034c26            -> 034c26
034c1e: e6f4bfff       mov r4,#0xffbf                
034c22: 64f4faf5       and 0xf5fa,r4                 
034c26: e6fc6eed       mov r12,#0xed6e               
034c2a: e6fd3caa       mov r13,#0xaa3c               
034c2e: c2fe5502       movbz r14,0x255               
034c32: c2ff5502       movbz r15,0x255               
034c36: da025679       calls 0x027956                 -> 027956
034c3a: 4a1c2763       bmov 0xfd4e.0x3,0xfd38.0x6    
034c3e: c2f478ed       movbz r4,0xed78               
034c42: 66f42000       and r4,#0x20                  
034c46: 2d04           jmpr cc_EQ,0x034c50            -> 034c50
034c48: e6f47fff       mov r4,#0xff7f                
034c4c: 64f4faf5       and 0xf5fa,r4                 
034c50: e6fc7aed       mov r12,#0xed7a               
034c54: e6fd4caa       mov r13,#0xaa4c               
034c58: c2fe5502       movbz r14,0x255               
034c5c: c2ff5502       movbz r15,0x255               
034c60: da025679       calls 0x027956                 -> 027956
034c64: 4a1c2764       bmov 0xfd4e.0x4,0xfd38.0x6    
034c68: c2f484ed       movbz r4,0xed84               
034c6c: 66f42000       and r4,#0x20                  
034c70: 2d04           jmpr cc_EQ,0x034c7a            -> 034c7a
034c72: e6f4fffe       mov r4,#0xfeff                
034c76: 64f4faf5       and 0xf5fa,r4                 
034c7a: e6fc86ed       mov r12,#0xed86               
034c7e: e6fd5caa       mov r13,#0xaa5c               
034c82: c2fe5502       movbz r14,0x255               
034c86: c2ff5502       movbz r15,0x255               
034c8a: da025679       calls 0x027956                 -> 027956
034c8e: 4a1c2765       bmov 0xfd4e.0x5,0xfd38.0x6    
034c92: c2f490ed       movbz r4,0xed90               
034c96: 66f42000       and r4,#0x20                  
034c9a: 2d04           jmpr cc_EQ,0x034ca4            -> 034ca4
034c9c: e6f4fffd       mov r4,#0xfdff                
034ca0: 64f4faf5       and 0xf5fa,r4                 
034ca4: f68e36f5       mov 0xf536,0xff1c             
034ca8: f78e4ef5       movb 0xf54e,0xff1c            
034cac: f78e50f5       movb 0xf550,0xff1c            
034cb0: f78e4ff5       movb 0xf54f,0xff1c            
034cb4: f78e51f5       movb 0xf551,0xff1c            
034cb8: f68e1cf5       mov 0xf51c,0xff1c             
034cbc: f68e1ef5       mov 0xf51e,0xff1c             
034cc0: f68e20f5       mov 0xf520,0xff1c             
034cc4: f68e22f5       mov 0xf522,0xff1c             
034cc8: f68e24f5       mov 0xf524,0xff1c             
034ccc: f68e26f5       mov 0xf526,0xff1c             
034cd0: f3f81af5       movb RL4,0xf51a               
034cd4: 4980           cmpb RL4,#0x0                 
034cd6: fd02           jmpr cc_ULE,0x034cdc           -> 034cdc
034cd8: 4f29           bset 0xfd52.0x4               
034cda: 0d01           jmpr cc_UC,0x034cde            -> 034cde
034cdc: 4e29           bclr 0xfd52.0x4               
034cde: e014           mov r4,#0x1                   
034ce0: 04f438f5       add 0xf538,r4                 
034ce4: f2f438f5       mov r4,0xf538                 
034ce8: 46f4b80b       cmp r4,#0xbb8                 
034cec: ea30e010       jmpa cc_NE,0x0310e0            -> 0310e0
034cf0: 9a2c0eb0       jnb 0xfd58.0xb,0x034d10        -> 034d10
034cf4: e7f82000       movb RL4,#0x20                
034cf8: 75f854ed       orb 0xed54,RL4                
034cfc: 75f860ed       orb 0xed60,RL4                
034d00: 75f86ced       orb 0xed6c,RL4                
034d04: 75f878ed       orb 0xed78,RL4                
034d08: 75f884ed       orb 0xed84,RL4                
034d0c: 75f890ed       orb 0xed90,RL4                
034d10: f2f434f5       mov r4,0xf534                 
034d14: 42f46c00       cmp r4,0x6c                   
034d18: fd01           jmpr cc_ULE,0x034d1c           -> 034d1c
034d1a: ff29           bset 0xfd52.0xf               
034d1c: f2f434f5       mov r4,0xf534                 
034d20: 42f41401       cmp r4,0x114                  
034d24: fd0b           jmpr cc_ULE,0x034d3c           -> 034d3c
034d26: e6fc28f5       mov r12,#0xf528               
034d2a: e06d           mov r13,#0x6                  
034d2c: da038048       calls 0x034880                 -> 034880
034d30: c089           movbz r9,RL4                  
034d32: af29           bset 0xfd52.0xa               
034d34: e118           movb RL4,#0x1                 
034d36: 05f866f4       addb 0xf466,RL4               
034d3a: 0d01           jmpr cc_UC,0x034d3e            -> 034d3e
034d3c: ae29           bclr 0xfd52.0xa               
034d3e: f3f854f5       movb RL4,0xf554               
034d42: f7f862e8       movb 0xe862,RL4               
034d46: f3fa53f5       movb RL5,0xf553               
034d4a: f7fa63e8       movb 0xe863,RL5               
034d4e: f3f655f5       movb RL3,0xf555               
034d52: f7f664e8       movb 0xe864,RL3               
034d56: 8a2902a0       jb 0xfd52.0xa,0x034d5e         -> 034d5e
034d5a: ea00b40f       jmpa cc_UC,0x030fb4            -> 030fb4
034d5e: f2f428f5       mov r4,0xf528                 
034d62: 42f47000       cmp r4,0x70                   
034d66: ed02           jmpr cc_UGT,0x034d6c           -> 034d6c
034d68: 4890           cmp r9,#0x0                   
034d6a: 3d12           jmpr cc_NE,0x034d90            -> 034d90
034d6c: e6fc4400       mov r12,#0x44                 
034d70: da039e1c       calls 0x031c9e                 -> 031c9e
034d74: c2f454ed       movbz r4,0xed54               
034d78: 66f40800       and r4,#0x8                   
034d7c: 2d06           jmpr cc_EQ,0x034d8a            -> 034d8a
034d7e: da03921d       calls 0x031d92                 -> 031d92
034d82: f3f8d4ab       movb RL4,0xabd4               
034d86: 75f846f5       orb 0xf546,RL4                
034d8a: e118           movb RL4,#0x1                 
034d8c: f7f80eea       movb 0xea0e,RL4               
034d90: e6fc4aed       mov r12,#0xed4a               
034d94: e6fd0caa       mov r13,#0xaa0c               
034d98: c2fe5502       movbz r14,0x255               
034d9c: c2ff5502       movbz r15,0x255               
034da0: da025679       calls 0x027956                 -> 027956
034da4: 4a1c2760       bmov 0xfd4e.0x0,0xfd38.0x6    
034da8: c2f454ed       movbz r4,0xed54               
034dac: 66f42000       and r4,#0x20                  
034db0: 2d04           jmpr cc_EQ,0x034dba            -> 034dba
034db2: e6f4efff       mov r4,#0xffef                
034db6: 64f4faf5       and 0xf5fa,r4                 
034dba: f2f42af5       mov r4,0xf52a                 
034dbe: 42f47000       cmp r4,0x70                   
034dc2: ed02           jmpr cc_UGT,0x034dc8           -> 034dc8
034dc4: 4891           cmp r9,#0x1                   
034dc6: 3d12           jmpr cc_NE,0x034dec            -> 034dec
034dc8: e6fc4500       mov r12,#0x45                 
034dcc: da039e1c       calls 0x031c9e                 -> 031c9e
034dd0: c2f460ed       movbz r4,0xed60               
034dd4: 66f40800       and r4,#0x8                   
034dd8: 2d06           jmpr cc_EQ,0x034de6            -> 034de6
034dda: da03921d       calls 0x031d92                 -> 031d92
034dde: f3f8d5ab       movb RL4,0xabd5               
034de2: 75f846f5       orb 0xf546,RL4                
034de6: e118           movb RL4,#0x1                 
034de8: f7f80eea       movb 0xea0e,RL4               
034dec: e6fc56ed       mov r12,#0xed56               
034df0: e6fd1caa       mov r13,#0xaa1c               
034df4: c2fe5502       movbz r14,0x255               
034df8: c2ff5502       movbz r15,0x255               
034dfc: da025679       calls 0x027956                 -> 027956
034e00: 4a1c2761       bmov 0xfd4e.0x1,0xfd38.0x6    
034e04: c2f460ed       movbz r4,0xed60               
034e08: 66f42000       and r4,#0x20                  
034e0c: 2d04           jmpr cc_EQ,0x034e16            -> 034e16
034e0e: e6f4dfff       mov r4,#0xffdf                
034e12: 64f4faf5       and 0xf5fa,r4                 
034e16: f2f42cf5       mov r4,0xf52c                 
034e1a: 42f47000       cmp r4,0x70                   
034e1e: ed02           jmpr cc_UGT,0x034e24           -> 034e24
034e20: 4892           cmp r9,#0x2                   
034e22: 3d12           jmpr cc_NE,0x034e48            -> 034e48
034e24: e6fc4600       mov r12,#0x46                 
034e28: da039e1c       calls 0x031c9e                 -> 031c9e
034e2c: c2f46ced       movbz r4,0xed6c               
034e30: 66f40800       and r4,#0x8                   
034e34: 2d06           jmpr cc_EQ,0x034e42            -> 034e42
034e36: da03921d       calls 0x031d92                 -> 031d92
034e3a: f3f8d6ab       movb RL4,0xabd6               
034e3e: 75f846f5       orb 0xf546,RL4                
034e42: e118           movb RL4,#0x1                 
034e44: f7f80eea       movb 0xea0e,RL4               
034e48: e6fc62ed       mov r12,#0xed62               
034e4c: e6fd2caa       mov r13,#0xaa2c               
034e50: c2fe5502       movbz r14,0x255               
034e54: c2ff5502       movbz r15,0x255               
034e58: da025679       calls 0x027956                 -> 027956
034e5c: 4a1c2762       bmov 0xfd4e.0x2,0xfd38.0x6    
034e60: c2f46ced       movbz r4,0xed6c               
034e64: 66f42000       and r4,#0x20                  
034e68: 2d04           jmpr cc_EQ,0x034e72            -> 034e72
034e6a: e6f4bfff       mov r4,#0xffbf                
034e6e: 64f4faf5       and 0xf5fa,r4                 
034e72: f2f42ef5       mov r4,0xf52e                 
034e76: 42f47000       cmp r4,0x70                   
034e7a: ed02           jmpr cc_UGT,0x034e80           -> 034e80
034e7c: 4893           cmp r9,#0x3                   
034e7e: 3d12           jmpr cc_NE,0x034ea4            -> 034ea4
034e80: e6fc4700       mov r12,#0x47                 
034e84: da039e1c       calls 0x031c9e                 -> 031c9e
034e88: c2f478ed       movbz r4,0xed78               
034e8c: 66f40800       and r4,#0x8                   
034e90: 2d06           jmpr cc_EQ,0x034e9e            -> 034e9e
034e92: da03921d       calls 0x031d92                 -> 031d92
034e96: f3f8d7ab       movb RL4,0xabd7               
034e9a: 75f846f5       orb 0xf546,RL4                
034e9e: e118           movb RL4,#0x1                 
034ea0: f7f80eea       movb 0xea0e,RL4               
034ea4: e6fc6eed       mov r12,#0xed6e               
034ea8: e6fd3caa       mov r13,#0xaa3c               
034eac: c2fe5502       movbz r14,0x255               
034eb0: c2ff5502       movbz r15,0x255               
034eb4: da025679       calls 0x027956                 -> 027956
034eb8: 4a1c2763       bmov 0xfd4e.0x3,0xfd38.0x6    
034ebc: c2f478ed       movbz r4,0xed78               
034ec0: 66f42000       and r4,#0x20                  
034ec4: 2d04           jmpr cc_EQ,0x034ece            -> 034ece
034ec6: e6f47fff       mov r4,#0xff7f                
034eca: 64f4faf5       and 0xf5fa,r4                 
034ece: f2f430f5       mov r4,0xf530                 
034ed2: 42f47000       cmp r4,0x70                   
034ed6: ed02           jmpr cc_UGT,0x034edc           -> 034edc
034ed8: 4894           cmp r9,#0x4                   
034eda: 3d12           jmpr cc_NE,0x034f00            -> 034f00
034edc: e6fc4800       mov r12,#0x48                 
034ee0: da039e1c       calls 0x031c9e                 -> 031c9e
034ee4: c2f484ed       movbz r4,0xed84               
034ee8: 66f40800       and r4,#0x8                   
034eec: 2d06           jmpr cc_EQ,0x034efa            -> 034efa
034eee: da03921d       calls 0x031d92                 -> 031d92
034ef2: f3f8d8ab       movb RL4,0xabd8               
034ef6: 75f846f5       orb 0xf546,RL4                
034efa: e118           movb RL4,#0x1                 
034efc: f7f80eea       movb 0xea0e,RL4               
034f00: e6fc7aed       mov r12,#0xed7a               
034f04: e6fd4caa       mov r13,#0xaa4c               
034f08: c2fe5502       movbz r14,0x255               
034f0c: c2ff5502       movbz r15,0x255               
034f10: da025679       calls 0x027956                 -> 027956
034f14: 4a1c2764       bmov 0xfd4e.0x4,0xfd38.0x6    
034f18: c2f484ed       movbz r4,0xed84               
034f1c: 66f42000       and r4,#0x20                  
034f20: 2d04           jmpr cc_EQ,0x034f2a            -> 034f2a
034f22: e6f4fffe       mov r4,#0xfeff                
034f26: 64f4faf5       and 0xf5fa,r4                 
034f2a: f2f432f5       mov r4,0xf532                 
034f2e: 42f47000       cmp r4,0x70                   
034f32: ed02           jmpr cc_UGT,0x034f38           -> 034f38
034f34: 4895           cmp r9,#0x5                   
034f36: 3d12           jmpr cc_NE,0x034f5c            -> 034f5c
034f38: e6fc4900       mov r12,#0x49                 
034f3c: da039e1c       calls 0x031c9e                 -> 031c9e
034f40: c2f490ed       movbz r4,0xed90               
034f44: 66f40800       and r4,#0x8                   
034f48: 2d06           jmpr cc_EQ,0x034f56            -> 034f56
034f4a: da03921d       calls 0x031d92                 -> 031d92
034f4e: f3f8d9ab       movb RL4,0xabd9               
034f52: 75f846f5       orb 0xf546,RL4                
034f56: e118           movb RL4,#0x1                 
034f58: f7f80eea       movb 0xea0e,RL4               
034f5c: e6fc86ed       mov r12,#0xed86               
034f60: e6fd5caa       mov r13,#0xaa5c               
034f64: c2fe5502       movbz r14,0x255               
034f68: c2ff5502       movbz r15,0x255               
034f6c: da025679       calls 0x027956                 -> 027956
034f70: 4a1c2765       bmov 0xfd4e.0x5,0xfd38.0x6    
034f74: c2f490ed       movbz r4,0xed90               
034f78: 66f42000       and r4,#0x20                  
034f7c: 2d04           jmpr cc_EQ,0x034f86            -> 034f86
034f7e: e6f4fffd       mov r4,#0xfdff                
034f82: 64f4faf5       and 0xf5fa,r4                 
034f86: e009           mov r9,#0x0                   
034f88: f489d4ab       movb RL4,[r9+#0xabd4]         
034f8c: 63f846f5       andb RL4,0xf546               
034f90: 2d0a           jmpr cc_EQ,0x034fa6            -> 034fa6
034f92: f489daab       movb RL4,[r9+#0xabda]         
034f96: 63f846f5       andb RL4,0xf546               
034f9a: e6f547f5       mov r5,#0xf547                
034f9e: 0059           add r5,r9                     
034fa0: a965           movb RL3,[r5]                 
034fa2: 7168           orb RL3,RL4                   
034fa4: b965           movb [r5],RL3                 
034fa6: 0891           add r9,#0x1                   
034fa8: 4896           cmp r9,#0x6                   
034faa: 8dee           jmpr cc_C,0x034f88             -> 034f88
034fac: f78e46f5       movb 0xf546,0xff1c            
034fb0: ea00b010       jmpa cc_UC,0x0310b0            -> 0310b0
035028: 2d04           jmpr cc_EQ,0x035032            -> 035032
03502a: e6f4bfff       mov r4,#0xffbf                
03502e: 64f4faf5       and 0xf5fa,r4                 
035032: e6fc6eed       mov r12,#0xed6e               
035036: e6fd3caa       mov r13,#0xaa3c               
03503a: c2fe5502       movbz r14,0x255               
03503e: c2ff5502       movbz r15,0x255               
035042: da025679       calls 0x027956                 -> 027956
035046: 4a1c2763       bmov 0xfd4e.0x3,0xfd38.0x6    
03504a: c2f478ed       movbz r4,0xed78               
03504e: 66f42000       and r4,#0x20                  
035052: 2d04           jmpr cc_EQ,0x03505c            -> 03505c
035054: e6f47fff       mov r4,#0xff7f                
035058: 64f4faf5       and 0xf5fa,r4                 
03505c: e6fc7aed       mov r12,#0xed7a               
035060: e6fd4caa       mov r13,#0xaa4c               
035064: c2fe5502       movbz r14,0x255               
035068: c2ff5502       movbz r15,0x255               
03506c: da025679       calls 0x027956                 -> 027956
035070: 4a1c2764       bmov 0xfd4e.0x4,0xfd38.0x6    
035074: c2f484ed       movbz r4,0xed84               
035078: 66f42000       and r4,#0x20                  
03507c: 2d04           jmpr cc_EQ,0x035086            -> 035086
03507e: e6f4fffe       mov r4,#0xfeff                
035082: 64f4faf5       and 0xf5fa,r4                 
035086: e6fc86ed       mov r12,#0xed86               
03508a: e6fd5caa       mov r13,#0xaa5c               
03508e: c2fe5502       movbz r14,0x255               
035092: c2ff5502       movbz r15,0x255               
035096: da025679       calls 0x027956                 -> 027956
03509a: 4a1c2765       bmov 0xfd4e.0x5,0xfd38.0x6    
03509e: c2f490ed       movbz r4,0xed90               
0350a2: 66f42000       and r4,#0x20                  
0350a6: 2d04           jmpr cc_EQ,0x0350b0            -> 0350b0
0350a8: e6f4fffd       mov r4,#0xfdff                
0350ac: 64f4faf5       and 0xf5fa,r4                 
0350b0: f68e38f5       mov 0xf538,0xff1c             
0350b4: f78e52f5       movb 0xf552,0xff1c            
0350b8: f78e54f5       movb 0xf554,0xff1c            
0350bc: f78e53f5       movb 0xf553,0xff1c            
0350c0: f78e55f5       movb 0xf555,0xff1c            
0350c4: f68e28f5       mov 0xf528,0xff1c             
0350c8: f68e2af5       mov 0xf52a,0xff1c             
0350cc: f68e2cf5       mov 0xf52c,0xff1c             
0350d0: f68e2ef5       mov 0xf52e,0xff1c             
0350d4: f68e30f5       mov 0xf530,0xff1c             
0350d8: f68e32f5       mov 0xf532,0xff1c             
0350dc: f68e34f5       mov 0xf534,0xff1c             
0350e0: 9890           mov r9,[r0+]                  
0350e2: db00           rets                          
03547a: c2fd91ef       movbz r13,0xef91              
03547e: da03024b       calls 0x034b02                 -> 034b02
035482: e6fc1e33       mov r12,#0x331e               
035486: da03a64b       calls 0x034ba6                 -> 034ba6
03548a: f7f8d0fa       movb 0xfad0,RL4               
03548e: 8e2c           bclr 0xfd58.0x8               
035490: 7e2c           bclr 0xfd58.0x7               
035492: f78fcffa       movb 0xfacf,0xff1e            
035496: f78e56f5       movb 0xf556,0xff1c            
03549a: f78e57f5       movb 0xf557,0xff1c            
03549e: f78e58f5       movb 0xf558,0xff1c            
0354a2: f78e59f5       movb 0xf559,0xff1c            
0354a6: db00           rets                          
0357ae: f3f83df6       movb RL4,0xf63d               
0357b2: 6982           andb RL4,#0x2                 
0357b4: 2d05           jmpr cc_EQ,0x0357c0            -> 0357c0
0357b6: 4e2c           bclr 0xfd58.0x4               
0357b8: 5e2c           bclr 0xfd58.0x5               
0357ba: 6e2c           bclr 0xfd58.0x6               
0357bc: f78e3df6       movb 0xf63d,0xff1c            
0357c0: 9860           mov r6,[r0+]                  
0357c2: db00           rets                          
03583a: 67f84000       andb RL4,#0x40                
03583e: ea20401b       jmpa cc_EQ,0x031b40            -> 031b40
035842: 9a1c1330       jnb 0xfd38.0x3,0x03586c        -> 03586c
035846: f2f438fd       mov r4,0xfd38                 
03584a: 66f4fcff       and r4,#0xfffc                
03584e: f2f538fd       mov r5,0xfd38                 
035852: 6853           and r5,#0x3                   
035854: 2851           sub r5,#0x1                   
035856: 7045           or r4,r5                      
035858: f6f438fd       mov 0xfd38,r4                 
03585c: f2f438fd       mov r4,0xfd38                 
035860: 6843           and r4,#0x3                   
035862: ea30401b       jmpa cc_NE,0x031b40            -> 031b40
035866: 3e1c           bclr 0xfd38.0x3               
035868: ea00401b       jmpa cc_UC,0x031b40            -> 031b40
03586c: f2f438fd       mov r4,0xfd38                 
035870: 66f4fcff       and r4,#0xfffc                
035874: f6f438fd       mov 0xfd38,r4                 
035878: 9a1c0220       jnb 0xfd38.0x2,0x035880        -> 035880
03587c: ea00401b       jmpa cc_UC,0x031b40            -> 031b40
035880: 7e1c           bclr 0xfd38.0x7               
035882: ea00401b       jmpa cc_UC,0x031b40            -> 031b40
03593c: 6843           and r4,#0x3                   
03593e: ea30401b       jmpa cc_NE,0x031b40            -> 031b40
035942: 3e1c           bclr 0xfd38.0x3               
035944: ea00401b       jmpa cc_UC,0x031b40            -> 031b40
035b7c: db00           rets                          
03618e: c0e4           movbz r4,RL7                  
036190: f4a414ac       movb RL5,[r4+#0xac14]         
036194: c0a4           movbz r4,RL5                  
036196: f054           mov r5,r4                     
036198: 5c25           shl r5,#0x2                   
03619a: 2054           sub r5,r4                     
03619c: 5c25           shl r5,#0x2                   
03619e: f48524ea       movb RL4,[r5+#0xea24]         
0361a2: c084           movbz r4,RL4                  
0361a4: 66f48400       and r4,#0x84                  
0361a8: 46f48400       cmp r4,#0x84                  
0361ac: 3d0f           jmpr cc_NE,0x0361cc            -> 0361cc
0361ae: e7f81200       movb RL4,#0x12                
0361b2: f058           mov r5,r8                     
0361b4: 0881           add r8,#0x1                   
0361b6: c0a5           movbz r5,RL5                  
0361b8: e48548f7       movb [r5+#0xf748],RL4         
0361bc: e7f85000       movb RL4,#0x50                
0361c0: f058           mov r5,r8                     
0361c2: 0881           add r8,#0x1                   
0361c4: c0a5           movbz r5,RL5                  
0361c6: e48548f7       movb [r5+#0xf748],RL4         
0361ca: e1de           movb RL7,#0xd                 
0361cc: 09e1           addb RL7,#0x1                 
0361d0: 0d00           jmpr cc_UC,0x0361d2            -> 0361d2
0361d2: 8ddd           jmpr cc_C,0x03618e             -> 03618e
0361d4: e10c           movb RL6,#0x0                 
0361d6: e10e           movb RL7,#0x0                 
0361d8: c0e4           movbz r4,RL7                  
0361da: f4a447f5       movb RL5,[r4+#0xf547]         
0361de: 71ca           orb RL6,RL5                   
0361e0: 09e1           addb RL7,#0x1                 
0361e2: 49e6           cmpb RL7,#0x6                 
0361e4: 8df9           jmpr cc_C,0x0361d8             -> 0361d8
0361e6: c0cc           movbz r12,RL6                 
0361e8: da03c01d       calls 0x031dc0                 -> 031dc0
0361ec: 8a880260       jb 0xff10.0x6,0x0361f4         -> 0361f4
0361f0: ea008623       jmpa cc_UC,0x032386            -> 032386
0361f4: e138           movb RL4,#0x3                 
0361f6: f058           mov r5,r8                     
0361f8: 0881           add r8,#0x1                   
0361fa: c0a5           movbz r5,RL5                  
0361fc: e48548f7       movb [r5+#0xf748],RL4         
036200: e108           movb RL4,#0x0                 
036202: f058           mov r5,r8                     
036204: 0881           add r8,#0x1                   
036206: c0a5           movbz r5,RL5                  
036208: e48548f7       movb [r5+#0xf748],RL4         
03620c: ea008623       jmpa cc_UC,0x032386            -> 032386
03635e: d2a57c85       movbs 0xff4a,0x857c           
036362: f048           mov r4,r8                     
036364: 0881           add r8,#0x1                   
036366: c084           movbz r4,RL4                  
036368: e4a448f7       movb [r4+#0xf748],RL5         
03636c: c2f47af6       movbz r4,0xf67a               
036370: f4a492ee       movb RL5,[r4+#0xee92]         
036374: c0a4           movbz r4,RL5                  
036376: 5c44           shl r4,#0x4                   
036378: d454d2a5       mov r5,[r4+#0xa5d2]           
03637c: f048           mov r4,r8                     
03637e: 0881           add r8,#0x1                   
036380: c084           movbz r4,RL4                  
036382: e4a448f7       movb [r4+#0xf748],RL5         
036386: f048           mov r4,r8                     
036388: 4986           cmpb RL4,#0x6                 
03638a: ea801022       jmpa cc_C,0x032210             -> 032210
03638e: f3f853f7       movb RL4,0xf753               
036392: 4981           cmpb RL4,#0x1                 
036394: 3d0a           jmpr cc_NE,0x0363aa            -> 0363aa
036396: f3fa48f7       movb RL5,0xf748               
03639a: 3d07           jmpr cc_NE,0x0363aa            -> 0363aa
03639c: f3f649f7       movb RL3,0xf749               
0363a0: 3d04           jmpr cc_NE,0x0363aa            -> 0363aa
0363a2: e124           movb RL2,#0x2                 
0363a4: f7f453f7       movb 0xf753,RL2               
0363a8: 0d09           jmpr cc_UC,0x0363bc            -> 0363bc
0363aa: f3f87af6       movb RL4,0xf67a               
0363ae: 3d03           jmpr cc_NE,0x0363b6            -> 0363b6
0363b0: f78e53f7       movb 0xf753,0xff1c            
0363b4: 0d03           jmpr cc_UC,0x0363bc            -> 0363bc
0363b6: e118           movb RL4,#0x1                 
0363b8: f7f853f7       movb 0xf753,RL4               
0363bc: e1b8           movb RL4,#0xb                 
0363be: f7f850f7       movb 0xf750,RL4               
0363c2: f3fa55f7       movb RL5,0xf755               
0363c6: 77fa4000       orb RL5,#0x40                 
0363ca: f7fa47f7       movb 0xf747,RL5               
0363ce: ea006a25       jmpa cc_UC,0x03256a            -> 03256a
036658: 0d08           jmpr cc_UC,0x03666a            -> 03666a
036668: 4df7           jmpr cc_V,0x036658             -> 036658
03666a: e1b8           movb RL4,#0xb                 
03666c: f7f850f7       movb 0xf750,RL4               
036670: db00           rets                          
039d6a: ffff           bset r15.0xf                  
039d6c: ffff           bset r15.0xf                  
039d6e: ffff           bset r15.0xf                  
039d70: ffff           bset r15.0xf                  
039d72: ffff           bset r15.0xf                  
039d74: ffff           bset r15.0xf                  
039d76: ffff           bset r15.0xf                  
039d78: ffff           bset r15.0xf                  
039d7a: ffff           bset r15.0xf                  
039d7c: ffff           bset r15.0xf                  
039d7e: ffff           bset r15.0xf                  
039d80: ffff           bset r15.0xf                  
039d82: ffff           bset r15.0xf                  
039d84: ffff           bset r15.0xf                  
039d86: ffff           bset r15.0xf                  
039d88: ffff           bset r15.0xf                  
039d8a: ffff           bset r15.0xf                  
03a226: ffff           bset r15.0xf                  
03a228: ffff           bset r15.0xf                  
03a22a: ffff           bset r15.0xf                  
03a22c: ffff           bset r15.0xf                  
03a22e: ffff           bset r15.0xf                  
03a230: ffff           bset r15.0xf                  
03a232: ffff           bset r15.0xf                  
03a234: ffff           bset r15.0xf                  
03a236: ffff           bset r15.0xf                  
03a238: ffff           bset r15.0xf                  
03a23a: ffff           bset r15.0xf                  
03a23c: ffff           bset r15.0xf                  
03a23e: ffff           bset r15.0xf                  
03a240: ffff           bset r15.0xf                  
03a242: ffff           bset r15.0xf                  
03a244: ffff           bset r15.0xf                  
03a246: ffff           bset r15.0xf                  
03be18: ffff           bset r15.0xf                  
03be1a: ffff           bset r15.0xf                  
03be1c: ffff           bset r15.0xf                  
03be1e: ffff           bset r15.0xf                  
03be20: ffff           bset r15.0xf                  
03be22: ffff           bset r15.0xf                  
03be24: ffff           bset r15.0xf                  
03be26: ffff           bset r15.0xf                  
03be28: ffff           bset r15.0xf                  
03be2a: ffff           bset r15.0xf                  
03be2c: ffff           bset r15.0xf                  
03be2e: ffff           bset r15.0xf                  
03be30: ffff           bset r15.0xf                  
03be32: ffff           bset r15.0xf                  
03be34: ffff           bset r15.0xf                  
03be36: ffff           bset r15.0xf                  
03be38: ffff           bset r15.0xf                  
