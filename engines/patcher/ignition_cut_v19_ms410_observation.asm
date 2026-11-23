; V19 accepted-sample guard. Keep the native recovery entry offsets.
base 0x32F50
        calls 0x32D20        ; cut_active retains native cut freshness
        jmpr cc_NE,done
        movb RL4,0xD801
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,clear
        calls 0x32ea0
        jmpr cc_NE,clear
        movb RL4,#3
        orb  0xD802,RL4
done:
        rets
        nop
        nop
clear:
        mov r4,#0
        rets
