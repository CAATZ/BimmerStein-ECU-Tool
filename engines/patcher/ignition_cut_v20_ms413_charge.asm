; Evaluate both spark sources at the native charge transaction, sharing one
; calibration page switch. D802.6 admits Launch Spark. D802.5 records an omission.
base 0x3f800
        push r4
        movb RL4,0xD801
        andb RL4,#0xF0
        scxt DPP0,#4
        cmpb RL4,#0xA0
        jmpr cc_NE,restore_stock
        movb RL4,0x2a65
        jmpr cc_EQ,solo_rpm
        cmpb RL4,#0xFF
        jmpr cc_EQ,clear_solo
        cmpb RL4,#1
        jmpr cc_NE,pin81
        jnb 0xfd60.9,clear_solo
        jmpr cc_UC,solo_rpm
pin81:  cmpb RL4,#2
        jmpr cc_NE,pin82
        jnb 0xfd60.8,clear_solo
        jmpr cc_UC,solo_rpm
pin82:  cmpb RL4,#4
        jmpr cc_NE,clear_solo
        jnb 0xfd60.7,clear_solo
solo_rpm:
        movb RH4,0x2a66
        cmpb RH4,0xfc3c
        jmpr cc_ULE,solo_above
        movb RL4,0xD801
        jnb r4.0,clear_solo
        movb RL4,0x2a67
        cmpb RL4,RH4
        jmpr cc_NC,clear_solo
        subb RH4,RL4
        cmpb RH4,0xfc3c
        jmpr cc_UGT,clear_solo
solo_above:
        movb RL4,#1
        orb 0xD801,RL4
        movb RL4,0xD802
        jnb r4.6,clear_launch
        jmpr cc_UC,launch_enabled
clear_solo:
        movb RL4,0xD802
        jnb r4.6,both_off
        movb RL4,#0xFE
        andb 0xD801,RL4
launch_enabled:
        jb 0xFD30.4,clear_launch
        jnb 0xfdb6.6,clear_launch
        movb RH4,0x47e3
        cmpb RH4,0xfc3c
        jmpr cc_ULE,launch_above
        movb RL4,0xD801
        jnb r4.1,clear_launch
        movb RL4,0x47e8
        cmpb RL4,RH4
        jmpr cc_NC,clear_launch
        subb RH4,RL4
        cmpb RH4,0xfc3c
        jmpr cc_UGT,clear_launch
launch_above:
        movb RL4,#2
        orb 0xD801,RL4
        jmpr cc_UC,cut
clear_launch:
        movb RL4,#0xFD
        andb 0xD801,RL4
        movb RL4,0xD801
        jb r4.0,cut
        jmpr cc_UC,ordinary
both_off:
        ; Neither spark source can omit this charge. Release both request bits
        ; without taking another pass through the launch decision.
        movb RL4,#0xFC
        andb 0xD801,RL4
ordinary:
        movb RL4,#0xDF
        andb 0xD802,RL4
restore_stock:
        pop DPP0
        pop r4
        andb 0xFF04,RL1
        rets
cut:    pop DPP0
        ; Invalidate both masks and the outstanding transaction even if the
        ; first foreground knock initialization has not run. Ticket 3 survives
        ; an intervening permitted charge. Timing alone owns the E7 marker.
        mov r4,#0x0300
        mov 0xD806,r4
        mov 0xD80A,0xFF1C
        movb RL4,#63
        orb 0xD802,RL4
        pop r4
        rets
