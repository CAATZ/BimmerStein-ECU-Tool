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
027956: 66f9ff03       and r9,#0x3ff                 
02795a: ea00503a       jmpa cc_UC,0x023a50            -> 023a50
02800a: 2000           sub r0,r0                     
02800e: 1e00           bclr 0xfd00.0x1               
028012: 3afcc449       bmovn 0xff88.0x9,r12.0x4      
028016: 2200f2f4       sub 0xfe00,0xf4f2             
02801a: e4e8c449       movb [r8+#0x49c4],RL7         
02801e: 24009890       sub 0x9098,0xfe00             
028022: db00           rets                          
02eef8: da036245       calls 0x034562                 -> 034562
02eefc: f0d4           mov r13,r4                    
02eefe: f2fcd8f0       mov r12,0xf0d8                
02ef02: da036245       calls 0x034562                 -> 034562
02ef06: 06f40080       add r4,#0x8000                
02ef0a: f6f4daf0       mov 0xf0da,r4                 
02ef0e: 0d44           jmpr cc_UC,0x02ef98            -> 02ef98
02ef98: da02583a       calls 0x023a58                 -> 023a58
02ef9c: f7f8c8fa       movb 0xfac8,RL4               
02efa0: db00           rets                          
02f5b0: e6fcd833       mov r12,#0x33d8               
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
030cce: da03f24b       calls 0x034bf2                 -> 034bf2
030cd2: f6f466f5       mov 0xf566,r4                 
030cd6: 42f46af5       cmp r4,0xf56a                 
030cda: fd10           jmpr cc_ULE,0x030cfc           -> 030cfc
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
034494: 5c14           shl r4,#0x1                   
034496: d454ccf4       mov r5,[r4+#0xf4cc]           
03449a: f489f2ab       movb RL4,[r9+#0xabf2]         
03449e: c084           movbz r4,RL4                  
0344a0: 5c14           shl r4,#0x1                   
0344a4: e4f44025       movb [r4+#0x2540],RH7         
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
039050: f78e5ee6       movb 0xe65e,0xff1c            
039054: 1e6f           bclr 0xfdde.0x1               
039056: 3e6f           bclr 0xfdde.0x3               
039058: 8e6f           bclr 0xfdde.0x8               
03905a: 2e6f           bclr 0xfdde.0x2               
03905c: 3ed8           bclr 0xffb0.0x3               
03905e: 7eb7           bclr 0xff6e.0x7               
039060: 7eb8           bclr 0xff70.0x7               
039062: 6fb7           bset 0xff6e.0x6               
039064: 6fb8           bset 0xff70.0x6               
039066: 4fd8           bset 0xffb0.0x4               
039068: 0d4e           jmpr cc_UC,0x039106            -> 039106
039106: f68eacfc       mov 0xfcac,0xff1c             
03910a: ea004cd3       jmpa cc_UC,0x03d34c            -> 03d34c
03959a: f6f1e0fd       mov 0xfde0,r1                 
03959e: e6600205       mov 0xfec0,#0x502             
0395a2: e6b97800       mov 0xff72,#0x78              
0395a6: e65c0101       mov 0xfeb8,#0x101             
0395aa: ea0070d7       jmpa cc_UC,0x03d770            -> 03d770
039786: 1a88c0f0       bfldh 0xff10,#0xf0,#0xc0      
03978a: c60850fb       scxt 0xfe10,#0xfb50           
03978e: 7e01           bclr 0xfd02.0x7               
039790: c2f0e2f6       movbz r0,0xf6e2               
039794: 66f01f00       and r0,#0x1f                  
039798: 46f01700       cmp r0,#0x17                  
03979c: ed06           jmpr cc_UGT,0x0397aa           -> 0397aa
03979e: f7f00ff7       movb 0xf70f,RL0               
0397a2: 06f0f7f6       add r0,#0xf6f7                
0397a6: a400e2f6       movb [r0],0xf6e2              
0397aa: 6eba           bclr 0xff74.0x6               
0397ac: 7fbb           bset 0xff76.0x7               
0397ae: fc08           pop 0xfe10                    
0397b0: 1a88f0f0       bfldh 0xff10,#0xf0,#0xf0      
0397b4: fb88           reti                          
03d34c: 66f40800       and r4,#0x8                   
03d350: 3d1a           jmpr cc_NE,0x03d386            -> 03d386
03d352: 9a2e18f0       jnb 0xfd5c.0xf,0x03d386        -> 03d386
03d356: f3f894f6       movb RL4,0xf694               
03d35a: 43f87ef1       cmpb RL4,0xf17e               
03d35e: fd13           jmpr cc_ULE,0x03d386           -> 03d386
03d360: de20           bclr 0xfd40.0xd               
03d362: ef20           bset 0xfd40.0xe               
03d364: ae20           bclr 0xfd40.0xa               
03d366: bf20           bset 0xfd40.0xb               
03d368: f68e98ef       mov 0xef98,0xff1c             
03d36c: f3f86a03       movb RL4,0x36a                
03d370: 3d02           jmpr cc_NE,0x03d376            -> 03d376
03d372: 6f09           bset 0xfd12.0x6               
03d374: db00           rets                          
03d376: f2f4eaf7       mov r4,0xf7ea                 
03d37a: 3d1a           jmpr cc_NE,0x03d3b0            -> 03d3b0
03d37c: c2f56a03       movbz r5,0x36a                
03d380: f6f5eaf7       mov 0xf7ea,r5                 
03d384: db00           rets                          
03d386: f68eeaf7       mov 0xf7ea,0xff1c             
03d38a: db00           rets                          
03d3b0: db00           rets                          
03d770: f6f4e2ef       mov 0xefe2,r4                 
03d774: 4840           cmp r4,#0x0                   
03d776: ad05           jmpr cc_SGT,0x03d782           -> 03d782
03d778: 3e20           bclr 0xfd40.0x3               
03d77a: f68ee2ef       mov 0xefe2,0xff1c             
03d77e: f68ee6ef       mov 0xefe6,0xff1c             
03d782: f68ee4ef       mov 0xefe4,0xff1c             
03d786: 0d35           jmpr cc_UC,0x03d7f2            -> 03d7f2
03d7f2: 9890           mov r9,[r0+]                  
03d7f4: db00           rets                          
