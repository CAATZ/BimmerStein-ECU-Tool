000060: 0901           addb RL0,#0x1                 
000062: c006           movbz r6,RL0                  
000064: 0066           add r6,r6                     
000066: d48632aa       mov r8,[r6+#0xaa32]           
00006a: 9c08           jmpi cc_UC,[r8]               
00007e: f7fc5ffa       movb 0xfa5f,RL6               
000082: d4620200       mov r6,[r2+#0x2]              
000086: 02f652fe       add r6,0xfe52                 
00008a: f6f68cfe       mov 0xfe8c,r6                 
00008e: 1aaa0c0f       bfldh 0xff54,#0xf,#0xc        
000092: fc88           pop 0xff10                    
000094: 0824           add r2,#0x4                   
000096: 0d0f           jmpr cc_UC,0x0000b6            -> 0000b6
0000a0: f7fc5ffa       movb 0xfa5f,RL6               
0000a4: d4620200       mov r6,[r2+#0x2]              
0000a8: 02f652fe       add r6,0xfe52                 
0000ac: f6f68cfe       mov 0xfe8c,r6                 
0000b0: 1aaa0c0f       bfldh 0xff54,#0xf,#0xc        
0000b4: fc88           pop 0xff10                    
0000b6: 490b           cmpb RL0,[r3]                 
0000b8: 3d14           jmpr cc_NE,0x0000e2            -> 0000e2
0000ba: 7ee0           bclr 0xffc0.0x7               
0000bc: c2f680fa       movbz r6,0xfa80               
0000ea: ea007a42       jmpa cc_UC,0x00427a            -> 00427a
0000ee: f2f8cafa       mov r8,0xfaca                 
0000f2: 02f8eaf0       add r8,0xf0ea                 
0000f6: 7c18           shr r8,#0x1                   
0000f8: f2f7ccfa       mov r7,0xfacc                 
0000fc: 02f7eaf0       add r7,0xf0ea                 
000100: 7c17           shr r7,#0x1                   
000102: 8a020c00       jb 0xfd04.0x0,0x00011e         -> 00011e
000106: 0aa90f00       bfldl 0xff52,#0x0,#0xf        
00010a: f068           mov r6,r8                     
00010c: 02f652fe       add r6,0xfe52                 
000110: f6f680fe       mov 0xfe80,r6                 
000114: 0ee0           bclr 0xffc0.0x0               
000116: 8ef0           bclr r0.0x8                   
000118: 8e02           bclr 0xfd04.0x8               
00011a: 0aa90f0d       bfldl 0xff52,#0xd,#0xf        
00011e: 8a020c10       jb 0xfd04.0x1,0x00013a         -> 00013a
000122: 0aa9f000       bfldl 0xff52,#0x0,#0xf0       
000126: f067           mov r6,r7                     
000128: 02f652fe       add r6,0xfe52                 
00012c: f6f682fe       mov 0xfe82,r6                 
000130: 1ee0           bclr 0xffc0.0x1               
000132: 9ef0           bclr r0.0x9                   
000134: 9e02           bclr 0xfd04.0x9               
000136: 0aa9f0d0       bfldl 0xff52,#0xd0,#0xf0      
00013a: 8a020c20       jb 0xfd04.0x2,0x000156         -> 000156
00013e: 1aa9000f       bfldh 0xff52,#0xf,#0x0        
000142: f068           mov r6,r8                     
000144: 02f652fe       add r6,0xfe52                 
000148: f6f684fe       mov 0xfe84,r6                 
00014c: 2ee0           bclr 0xffc0.0x2               
00014e: aef0           bclr r0.0xa                   
000150: ae02           bclr 0xfd04.0xa               
000152: 1aa90d0f       bfldh 0xff52,#0xf,#0xd        
000156: 8a020c30       jb 0xfd04.0x3,0x000172         -> 000172
00015a: 1aa900f0       bfldh 0xff52,#0xf0,#0x0       
00015e: f067           mov r6,r7                     
000160: 02f652fe       add r6,0xfe52                 
000164: f6f686fe       mov 0xfe86,r6                 
000168: 3ee0           bclr 0xffc0.0x3               
00016a: bef0           bclr r0.0xb                   
00016c: be02           bclr 0xfd04.0xb               
00016e: 1aa9d0f0       bfldh 0xff52,#0xf0,#0xd0      
000f70: 9c08           jmpi cc_UC,[r8]               
000f84: f7fc5ffa       movb 0xfa5f,RL6               
000f88: d4620200       mov r6,[r2+#0x2]              
000f8c: 02f652fe       add r6,0xfe52                 
000f90: f6f68cfe       mov 0xfe8c,r6                 
000f94: 1aaa0c0f       bfldh 0xff54,#0xf,#0xc        
000f98: fc88           pop 0xff10                    
000f9a: 0824           add r2,#0x4                   
000f9c: 0d0f           jmpr cc_UC,0x000fbc            -> 000fbc
000fa6: f7fc5ffa       movb 0xfa5f,RL6               
000faa: d4620200       mov r6,[r2+#0x2]              
000fae: 02f652fe       add r6,0xfe52                 
000fb2: f6f68cfe       mov 0xfe8c,r6                 
000fb6: 1aaa0c0f       bfldh 0xff54,#0xf,#0xc        
000fba: fc88           pop 0xff10                    
000fbc: 490b           cmpb RL0,[r3]                 
000fbe: 3d0f           jmpr cc_NE,0x000fde            -> 000fde
000fc0: 7ee0           bclr 0xffc0.0x7               
000fc2: c2f680fa       movbz r6,0xfa80               
000fc6: f4e6cca9       movb RL7,[r6+#0xa9cc]         
000fca: f7fe5efa       movb 0xfa5e,RL7               
000fce: d4630200       mov r6,[r3+#0x2]              
000fd2: 02f652fe       add r6,0xfe52                 
000fd6: f6f68efe       mov 0xfe8e,r6                 
000fda: 1aaac0f0       bfldh 0xff54,#0xf0,#0xc0      
000fde: 4909           cmpb RL0,[r1]                 
000fe0: 3d5f           jmpr cc_NE,0x0010a0            -> 0010a0
000fe2: f4e10100       movb RL7,[r1+#0x1]            
000fe6: e10f           movb RH7,#0x0                 
000fe8: d48752ab       mov r8,[r7+#0xab52]           
000fec: 9c08           jmpi cc_UC,[r8]               
0010a0: 9a001dc0       jnb 0xfd00.0xc,0x0010de        -> 0010de
0010a4: 4108           cmpb RL0,RL4                  
0010a6: 3d1b           jmpr cc_NE,0x0010de            -> 0010de
0010a8: c097           movbz r7,RH4                  
0010aa: d4875eab       mov r8,[r7+#0xab5e]           
0010ae: 9c08           jmpi cc_UC,[r8]               
0010de: 43f090fa       cmpb RL0,0xfa90               
0010e2: 3d02           jmpr cc_NE,0x0010e8            -> 0010e8
0010e4: 4fe2           bset 0xffc4.0x4               
0010e6: 0d04           jmpr cc_UC,0x0010f0            -> 0010f0
0010e8: 43f091fa       cmpb RL0,0xfa91               
0010ec: 3d01           jmpr cc_NE,0x0010f0            -> 0010f0
0010ee: 4ee2           bclr 0xffc4.0x4               
0010f0: fc08           pop 0xfe10                    
0010f2: 1a88f0f0       bfldh 0xff10,#0xf0,#0xf0      
0010f6: fb88           reti                          
