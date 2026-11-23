; Launch V19 ms410: qualify the latch before publishing either request.
base 0x32B00

        push DPP0
        push r4
        push r5
        mov  DPP0,#4

        movb RL5,0xD801
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,arm_gate
        movb RL5,#0xA0
        movb 0xD801,RL5

arm_gate:
        jb   0xFD30.4,disarm         ; native vehicle-speed fault
        movb RL4,0x3021
        jmpr cc_NE,speed_gate
        movb RL5,0x3023
        movb RL4,0x302C
        cmpb RL4,#0xFF
        jmpr cc_NE,check_a
        movb RL4,0x01D2
check_a:
        cmpb RL4,RL5
        jmpr cc_C,speed_gate
        movb RL4,0x302B
        cmpb RL4,#0xFF
        jmpr cc_NE,check_b
        movb RL4,0x01D1
check_b:
        cmpb RL4,RL5
        jmpr cc_NC,disarm           ; neither strict-below release can be reached
speed_gate:
        movb RL4,0xEDF4
        cmpb RL4,0x3025
        jmpr cc_NC,disarm
        movb RL5,0xE8D0
        cmpb RL5,0x3026
        jmpr cc_C,disarm

        movb RL5,0x3020
        jmpr cc_EQ,arm_speed        ; Always ignores clutch polarity
        cmpb RL5,#1
        jmpr cc_NE,pin81
        jb   0xFD50.9,pin_high
        jmpr cc_UC,pin_low
pin81:  cmpb RL5,#2
        jmpr cc_NE,pin82
        jb   0xFD50.8,pin_high
        jmpr cc_UC,pin_low
pin82:  cmpb RL5,#4
        jmpr cc_NE,disarm
        jb   0xFD50.7,pin_high
pin_low:
        movb RL5,0x3022
        cmpb RL5,#1
        jmpr cc_UGT,disarm
        jmpr cc_NE,request_gate
        jmpr cc_UC,arm_speed
pin_high:
        movb RL5,0x3022
        cmpb RL5,#1
        jmpr cc_UGT,disarm
        jmpr cc_EQ,request_gate
arm_speed:
        cmpb RL4,0x3024
        jmpr cc_NC,request_gate     ; retain a previously qualified latch
        bset 0xFD80.6
        jmpr cc_UC,request_gate
disarm: bclr 0xFD80.6
request_gate:
        jnb  0xFD80.6,clear_both
        movb RL4,0x3021
        jmpr cc_EQ,fuel_mode
        cmpb RL4,#1
        jmpr cc_NE,clear_both

        movb RL4,#0xFB
        andb 0xD801,RL4
        movb RL5,0x3023
        movb RL4,0xD801
        andb RL4,#2
        jmpr cc_EQ,spark_compare
        movb RL4,0x3028
        cmpb RL4,RL5
        jmpr cc_NC,spark_compare    ; erased or excessive hysteresis means zero
        subb RL5,RL4
spark_compare:
        movb RL4,0xFAE6
        cmpb RL4,RL5
        jmpr cc_C,clear_both
        movb RL4,#2
        orb  0xD801,RL4
        jmpr cc_UC,done

fuel_mode:
        jnb  0xFD12.15,clear_both
        movb RL4,#0xFD
        andb 0xD801,RL4
        movb RL4,#4
        orb  0xD801,RL4
        jmpr cc_UC,done
clear_both:
        movb RL4,#0xF9
        andb 0xD801,RL4
done:
        pop  r5
        pop  r4
        pop  DPP0
        jmps 0x032C80                ; relocated soft-limit cave
