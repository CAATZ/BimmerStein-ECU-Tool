; Compact entry table shared by the native stage publisher and injector ISR.
base 0x3f900
        jmpr cc_UC,candidate
        jmpr cc_UC,other
        jmpr cc_UC,zero
        jmpr cc_UC,injector
candidate:
        push PSW
        push r5
        movb RH5,#1
        jmpr cc_UC,classify
zero:
other:
        ; RL4 is the stock-selected count on all paths, including zero.
        push PSW
        push r5
        movb RH5,#0
classify:
        push DPP0
        mov DPP0,#4
        movb RL5,0xD801
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_NE,normal
        jnb 0xFD12.15,normal
        movb RL5,0xf013
        jmpr cc_EQ,normal
        ; Track the primary RPM stage even if an independent larger cut hides it.
        movb RL5,0xD80D
        cmpb RL5,#0xA5
        jmpr cc_EQ,custom
        jb 0xFD30.4,normal
        jnb 0xfdb6.6,normal
        movb RL5,0x352d
        jmpr cc_NE,normal
        movb RL5,0xD801
        andb RL5,#8
        jmpr cc_EQ,custom
        ; A lower Launch hard threshold can still change a native soft episode.
        movb RL5,0xfc3c
        cmpb RL5,0xdb87
        jmpr cc_NC,normal
        movb RL5,0x3533
        cmpb RL5,#0xFF
        jmpr cc_NE,hard_compare
        movb RL5,0x352f
        cmpb RL5,#0xFD
        jmpr cc_NC,normal
        addb RL5,#3
hard_compare:
        cmpb RL5,0xfc3c
        jmpr cc_UGT,normal
custom:
        movb RL5,#0xA5
        movb 0xD80D,RL5
        cmpb RH5,#0
        jmpr cc_EQ,native
        cmpb RL4,0xf012
        jmpr cc_ULE,native
        movb RL5,#4
        push PSW
        bclr 0xFF10.11
        orb 0xD801,RL5
        jmpr cc_UC,commit
normal:
        movb 0xD80D,0xFF1C
native:
        movb RL5,#0xFB
        push PSW
        bclr 0xFF10.11
        andb 0xD801,RL5
commit:
        movb 0xfc34,RL4
        pop PSW
        pop DPP0
        pop r5
        pop PSW
        movb RL4,RL4
        rets
injector:
        push r4
        movb RL4,0xD801
        andb RL4,#0xF4
        cmpb RL4,#0xA4
        jmpr cc_NE,emit
        jb 0xfd5e.8,emit
        ; RL1 already carries native cylinder/transition inhibits. RL2 is
        ; the stage mask. RH0 and RH3 select the scheduled compare outputs.
        movb RL4,RL1
        cpl r4
        andb RL4,RL2
        andb RL4,RH0
        andb RL4,RH3
        jmpr cc_EQ,emit
        calls 0x3f000
        movb RL4,#31
        orb 0xD802,RL4
emit:
        pop r4
        orb RL1,RL2
        movb 0xFD04,RL1
        jmps 0x59e8
