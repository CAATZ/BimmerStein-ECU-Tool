; Ignition Cut V8 - shared MS41.2/MS41.3 control and fixed-IPW hook.
;
; Called at file 0x2355A / CPU 0x2755A after both bank IPWs are published.
; Updates only the standalone E812.0 request.  Launch owns E812.1.
; CUT_HYST=FF means zero hysteresis. CUT_IPW=FFFF preserves stock IPW.
base 0x3DEA0

        push DPP0
        push r5
        push r6
        mov  DPP0,#4

        ; Initialize the dedicated scratch byte once, failing all requests off.
        movb RL5,0xE812
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,switch_gate
        movb RL5,#0xA0
        movb 0xE812,RL5

switch_gate:
        movb RL5,0x2A65              ; CUTSW
        cmpb RL5,#0xFF
        jmpr cc_EQ,clear_standalone
        cmpb RL5,#0
        jmpr cc_EQ,rpm_gate

        cmpb RL5,#1
        jmpr cc_NE,pin81
        movb RL5,0xFD61              ; pin 80 = fd60.9
        andb RL5,#0x02
        jmpr cc_EQ,clear_standalone
        jmpr cc_UC,rpm_gate
pin81:  cmpb RL5,#2
        jmpr cc_NE,pin82
        movb RL5,0xFD61              ; pin 81 = fd60.8
        andb RL5,#0x01
        jmpr cc_EQ,clear_standalone
        jmpr cc_UC,rpm_gate
pin82:  cmpb RL5,#4
        jmpr cc_NE,clear_standalone
        movb RL5,0xFD60              ; pin 82 = fd60.7
        andb RL5,#0x80
        jmpr cc_EQ,clear_standalone

rpm_gate:
        movb RL5,0xE812
        andb RL5,#0x01
        jmpr cc_EQ,enter_compare

        movb RL5,0x2A67              ; CUT_HYST, RPM/32
        cmpb RL5,#0xFF
        jmpr cc_EQ,zero_hyst
        movb RL6,0x2A66              ; CUTRPM
        cmpb RL6,RL5
        jmpr cc_C,hyst_underflow
        subb RL6,RL5
        jmpr cc_UC,release_compare
zero_hyst:
        movb RL6,0x2A66
        jmpr cc_UC,release_compare
hyst_underflow:
        movb RL6,#0
release_compare:
        movb RL5,0xFC3C              ; actual RPM/32
        cmpb RL5,RL6
        jmpr cc_C,clear_standalone
        jmpr cc_UC,set_standalone

enter_compare:
        movb RL5,0xFC3C
        cmpb RL5,0x2A66
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
        andb RL5,#0x03               ; standalone OR launch ignition
        jmpr cc_EQ,done
        mov  r5,0x2A68               ; CUT_IPW, uint16 LE
        cmp  r5,#0xFFFF
        jmpr cc_EQ,done
        mov  0xEF7E,r5               ; bank 1 published IPW
        mov  0xEF80,r5               ; bank 2 published IPW

done:   pop  r6
        pop  r5
        pop  DPP0
        mov  r4,0xEF7C               ; displaced instruction
        rets
