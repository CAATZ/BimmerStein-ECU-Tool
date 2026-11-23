; C167 ROM BSL supplies CP=FA00, DPP3=3 and ASC0 autobaud/8N1.
; The ROM receives these 32 bytes at FA40. No flash or board access here.
base 0xFA40
entry:
        mov r0,#0xf600
        mov r1,#0x400
receive:
        jnb S0RIC.7,receive
        movb RL2,S0RBUF
        bclr S0RIC.7
        movb [r0],RL2
        add r0,#1
        sub r1,#1
        jmpr cc_NE,receive
        jmps 0x00f600
        nop
