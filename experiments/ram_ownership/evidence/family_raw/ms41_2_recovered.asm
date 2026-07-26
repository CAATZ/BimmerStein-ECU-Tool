024512: ea005806       jmpa cc_UC,0x020658            -> 020658
024660: 4a0c2122       bmov 0xfd42.0x2,0xfd18.0x2    
0246ae: 4a1c1a6b       bmov 0xfd34.0xb,0xfd38.0x6    
0246b2: c2f428ec       movbz r4,0xec28               
0246b6: 66f42000       and r4,#0x20                  
0246ba: 2d07           jmpr cc_EQ,0x0246ca            -> 0246ca
0246bc: e6f4fff7       mov r4,#0xf7ff                
0246c0: 64f4f6f5       and 0xf5f6,r4                 
0246c4: 0d02           jmpr cc_UC,0x0246ca            -> 0246ca
02a55a: e6fc0405       mov r12,#0x504                
02a55e: da036a4a       calls 0x034a6a                 -> 034a6a
02abe0: 4a1c1861       bmov 0xfd30.0x1,0xfd38.0x6    
02abe4: c2f430ea       movbz r4,0xea30               
02abe8: 66f42000       and r4,#0x20                  
02abec: ea2004ed       jmpa cc_EQ,0x02ed04            -> 02ed04
02abf0: e6f4fdff       mov r4,#0xfffd                
02abf4: 64f4f2f5       and 0xf5f2,r4                 
02abf8: ea0004ed       jmpa cc_UC,0x02ed04            -> 02ed04
02abfe: e6fc1aea       mov r12,#0xea1a               
02ac02: e6fdcca5       mov r13,#0xa5cc               
02ac54: 4a1c1861       bmov 0xfd30.0x1,0xfd38.0x6    
02ac58: c2f430ea       movbz r4,0xea30               
02ac5c: 66f42000       and r4,#0x20                  
02ac60: 2d1c           jmpr cc_EQ,0x02ac9a            -> 02ac9a
02ac62: e6f4fdff       mov r4,#0xfffd                
02ac66: 64f4f2f5       and 0xf5f2,r4                 
02ac6a: 0d17           jmpr cc_UC,0x02ac9a            -> 02ac9a
02ac84: 4a1c1861       bmov 0xfd30.0x1,0xfd38.0x6    
02ac88: c2f430ea       movbz r4,0xea30               
02ac8c: 66f42000       and r4,#0x20                  
02ac90: 2d04           jmpr cc_EQ,0x02ac9a            -> 02ac9a
02ac92: e6f4fdff       mov r4,#0xfffd                
02ac96: 64f4f2f5       and 0xf5f2,r4                 
02aca6: f7f81bea       movb 0xea1b,RL4               
02acfc: e6f4feff       mov r4,#0xfffe                
02ad00: 64f4f2f5       and 0xf5f2,r4                 
