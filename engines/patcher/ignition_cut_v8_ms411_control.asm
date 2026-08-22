; Ignition Cut V8 - MS41.1 control and fixed-IPW hook.
; Called at file 0x23A5A / CPU 0x27A5A after both bank IPWs are published.
base 0x3F8C0

        push DPP0
        push r5
        push r6
        mov  DPP0,#4

        movb RL5,0xE812
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,switch_gate
        movb RL5,#0xA0
        movb 0xE812,RL5

switch_gate:
        movb RL5,0x3700              ; CUTSW
        cmpb RL5,#0xFF
        jmpr cc_EQ,clear_standalone
        cmpb RL5,#0
        jmpr cc_EQ,rpm_gate

        cmpb RL5,#1
        jmpr cc_NE,pin81
        movb RL5,0xFD61
        andb RL5,#0x02
        jmpr cc_EQ,clear_standalone
        jmpr cc_UC,rpm_gate
pin81:  cmpb RL5,#2
        jmpr cc_NE,pin82
        movb RL5,0xFD61
        andb RL5,#0x01
        jmpr cc_EQ,clear_standalone
        jmpr cc_UC,rpm_gate
pin82:  cmpb RL5,#4
        jmpr cc_NE,clear_standalone
        movb RL5,0xFD60
        andb RL5,#0x80
        jmpr cc_EQ,clear_standalone

rpm_gate:
        movb RL5,0xE812
        andb RL5,#0x01
        jmpr cc_EQ,enter_compare

        movb RL5,0x3702              ; CUT_HYST, RPM/32
        cmpb RL5,#0xFF
        jmpr cc_EQ,zero_hyst
        movb RL6,0x3701              ; CUTRPM
        cmpb RL6,RL5
        jmpr cc_C,hyst_underflow
        subb RL6,RL5
        jmpr cc_UC,release_compare
zero_hyst:
        movb RL6,0x3701
        jmpr cc_UC,release_compare
hyst_underflow:
        movb RL6,#0
release_compare:
        movb RL5,0xFC3C
        cmpb RL5,RL6
        jmpr cc_C,clear_standalone
        jmpr cc_UC,set_standalone

enter_compare:
        movb RL5,0xFC3C
        cmpb RL5,0x3701
        jmpr cc_C,clear_standalone

set_standalone:
        movb RL5,#0x01
        orb  0xE812,RL5
        jmpr cc_UC,fixed_ipw
clear_standalone:
        movb RL5,#0xFE
        andb 0xE812,RL5

fixed_ipw:
        movb RL5,0xE812
        andb RL5,#0x03
        jmpr cc_EQ,done
        mov  r5,0x3703               ; CUT_IPW
        cmp  r5,#0xFFFF
        jmpr cc_EQ,done
        mov  0xEF96,r5
        mov  0xEF98,r5

done:   pop  r6
        pop  r5
        pop  DPP0
        mov  r4,0xEF94               ; displaced instruction
        rets
