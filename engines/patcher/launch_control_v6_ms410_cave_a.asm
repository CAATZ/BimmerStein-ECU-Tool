; Launch Control V6 - MS41.0 arm state and independent cut requests.
; Pins 80/81/82 are latched at FD50.9/.8/.7 on this firmware.
base 0x32B00

        push DPP0
        push r4
        push r5
        push r6
        mov  DPP0,#4

        movb RL5,0xE812
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,arm_gate
        movb RL5,#0xA0
        movb 0xE812,RL5

arm_gate:
        movb RL4,0x3020
        cmpb RL4,#0xFF
        jmpr cc_EQ,disarm
        cmpb RL4,#0
        jmpr cc_EQ,arm
        movb RL4,0xEDF4              ; vehicle speed
        cmpb RL4,0x3025
        jmpr cc_NC,disarm
        movb RL4,0xE8D0
        cmpb RL4,0x3026
        jmpr cc_C,disarm
        movb RL4,0xEDF4
        cmpb RL4,0x3024
        jmpr cc_NC,request_gate

        movb RL4,0x3020
        cmpb RL4,#1
        jmpr cc_NE,pin81
        movb RL4,0xFD51
        andb RL4,#0x02
        jmpr cc_UC,polarity
pin81:  cmpb RL4,#2
        jmpr cc_NE,pin82
        movb RL4,0xFD51
        andb RL4,#0x01
        jmpr cc_UC,polarity
pin82:  cmpb RL4,#4
        jmpr cc_NE,disarm
        movb RL4,0xFD50
        andb RL4,#0x80

polarity:
        movb RL5,0x3022
        cmpb RL5,#0
        jmpr cc_EQ,active_high
        cmpb RL4,#0
        jmpr cc_EQ,arm
        jmpr cc_UC,request_gate
active_high:
        cmpb RL4,#0
        jmpr cc_NE,arm
        jmpr cc_UC,request_gate

arm:    bset 0xFD5A.6
        jmpr cc_UC,request_gate
disarm:
        bclr 0xFD5A.6

request_gate:
        movb RL4,0xFD5A
        andb RL4,#0x40
        jmpr cc_EQ,clear_both
        movb RL4,0x3021
        cmpb RL4,#0
        jmpr cc_EQ,fuel_mode
        cmpb RL4,#1
        jmpr cc_NE,clear_both

ignition_mode:
        movb RL4,#0xFB
        andb 0xE812,RL4
        movb RL4,0xE812
        andb RL4,#0x02
        jmpr cc_EQ,spark_enter
        movb RL4,0x3012
        cmpb RL4,#0xFF
        jmpr cc_EQ,spark_zero_hyst
        movb RL5,0x3023
        cmpb RL5,RL4
        jmpr cc_C,spark_underflow
        subb RL5,RL4
        jmpr cc_UC,spark_release
spark_zero_hyst:
        movb RL5,0x3023
        jmpr cc_UC,spark_release
spark_underflow:
        movb RL5,#0
spark_release:
        movb RL4,0xFAE6
        cmpb RL4,RL5
        jmpr cc_C,clear_spark
        jmpr cc_UC,set_spark
spark_enter:
        movb RL4,0xFAE6
        cmpb RL4,0x3023
        jmpr cc_C,clear_spark
set_spark:
        movb RL4,#0x02
        orb  0xE812,RL4
        jmpr cc_UC,done
clear_spark:
        movb RL4,#0xFD
        andb 0xE812,RL4
        jmpr cc_UC,done

fuel_mode:
        movb RL4,#0xFD
        andb 0xE812,RL4
        movb RL4,0xFD13
        andb RL4,#0x80
        jmpr cc_EQ,clear_fuel
        movb RL4,#0x04
        orb  0xE812,RL4
        jmpr cc_UC,done
clear_fuel:
        movb RL4,#0xFB
        andb 0xE812,RL4
        jmpr cc_UC,done
clear_both:
        movb RL4,#0xF9
        andb 0xE812,RL4

done:   pop  r6
        pop  r5
        pop  r4
        pop  DPP0
        jmps 0x032C80                ; relocated soft-limit cave
