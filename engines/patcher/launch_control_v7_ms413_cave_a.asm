; Launch Control V7 - MS41.3 arm state and independent cut requests.
;
; FDB6.6 is the collision-safe launch-armed latch. E847.1 is the launch ignition-cut
; request consumed by Ignition Cut V9; E847.2 marks actual launch fuel cut.
; Each request is owned here and remains stable until its release condition.
base 0x3DF80

        push DPP0
        push r4
        push r5
        push r6
        mov  DPP0,#4

        movb RL5,0xE847
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,arm_gate
        movb RL5,#0xA0
        movb 0xE847,RL5

arm_gate:
        movb RL4,0x47E0              ; LC_SW
        cmpb RL4,#0xFF
        jmpr cc_EQ,disarm
        cmpb RL4,#0
        jmpr cc_EQ,arm

        movb RL4,0xF19A              ; vehicle speed
        cmpb RL4,0x47E5              ; LC_MAXSPEED
        jmpr cc_NC,disarm
        movb RL4,0xE8D0              ; TPS
        cmpb RL4,0x47E6              ; LC_MINTPS
        jmpr cc_C,disarm
        movb RL4,0xF19A
        cmpb RL4,0x47E4              ; LC_ARMSPEED
        jmpr cc_NC,request_gate       ; preserve existing latch during rollout

        movb RL4,0x47E0
        cmpb RL4,#1
        jmpr cc_NE,pin81
        movb RL4,0xFD61
        andb RL4,#0x02
        jmpr cc_UC,polarity
pin81:  cmpb RL4,#2
        jmpr cc_NE,pin82
        movb RL4,0xFD61
        andb RL4,#0x01
        jmpr cc_UC,polarity
pin82:  cmpb RL4,#4
        jmpr cc_NE,disarm
        movb RL4,0xFD60
        andb RL4,#0x80

polarity:
        movb RL5,0x47E2              ; LC_CLUTCHPOL
        cmpb RL5,#0
        jmpr cc_EQ,active_high
        cmpb RL4,#0
        jmpr cc_EQ,arm
        jmpr cc_UC,request_gate
active_high:
        cmpb RL4,#0
        jmpr cc_NE,arm
        jmpr cc_UC,request_gate

; FDB6 is hash-certified free post-startup IRAM on canonical SS1v2.
arm:    bset 0xFDB6.6
        jmpr cc_UC,request_gate
disarm:
        bclr 0xFDB6.6

request_gate:
        movb RL4,0xFDB6
        andb RL4,#0x40
        jmpr cc_EQ,clear_both
        movb RL4,0x47E1              ; LC_CUTTYPE
        cmpb RL4,#0
        jmpr cc_EQ,fuel_mode
        cmpb RL4,#1
        jmpr cc_NE,clear_both

ignition_mode:
        movb RL4,#0xFB               ; launch fuel request off
        andb 0xE847,RL4
        movb RL4,0xE847
        andb RL4,#0x02
        jmpr cc_EQ,spark_enter

        movb RL4,0x47E8              ; LC_HYST
        cmpb RL4,#0xFF
        jmpr cc_EQ,spark_zero_hyst
        movb RL5,0x47E3              ; LC_MAXRPM
        cmpb RL5,RL4
        jmpr cc_C,spark_zero_hyst
        jmpr cc_EQ,spark_zero_hyst
        subb RL5,RL4
        jmpr cc_UC,spark_release
spark_zero_hyst:
        movb RL5,0x47E3
        jmpr cc_UC,spark_release
spark_release:
        movb RL4,0xFC3C
        cmpb RL4,RL5
        jmpr cc_C,clear_spark
        jmpr cc_UC,set_spark

spark_enter:
        movb RL4,0xFC3C
        cmpb RL4,0x47E3
        jmpr cc_C,clear_spark
set_spark:
        movb RL4,#0x02
        orb  0xE847,RL4
        jmpr cc_UC,done
clear_spark:
        movb RL4,#0xFD
        andb 0xE847,RL4
        jmpr cc_UC,done

fuel_mode:
        movb RL4,#0xFD               ; launch spark request off
        andb 0xE847,RL4
        movb RL4,0xFD13              ; FD12.15 stock limiter-active latch
        andb RL4,#0x80
        jmpr cc_EQ,clear_fuel
        movb RL4,#0x04
        orb  0xE847,RL4
        jmpr cc_UC,done
clear_fuel:
        movb RL4,#0xFB
        andb 0xE847,RL4
        jmpr cc_UC,done

clear_both:
        movb RL4,#0xF9
        andb 0xE847,RL4

done:   pop  r6
        pop  r5
        pop  r4
        pop  DPP0
        movb RL4,0xF19A              ; displaced instruction
        jmps 0x03992C
