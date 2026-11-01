; V18 request/recovery owner. The following hook calls the bounded fuel owner.
; V17 initializes knock provenance before publishing the runtime marker.
base 0x32A00

        push DPP0
        push r5
        push r6
        mov  DPP0,#4

        movb RL5,0xD801
        andb RL5,#0xF0
        cmpb RL5,#0xA0
        jmpr cc_EQ,switch_gate
        mov r5,#0xC000
        mov 0xD802,r5              ; freshness=0, timing=C0
        movb 0xD804,RL5            ; no timing source before marker
        calls 0x33600
        movb RL5,#0xA0
        movb 0xD80C,RL5
        movb 0xD801,RL5

switch_gate:
        movb RL5,0x3010              ; CUTSW
        cmpb RL5,#0xFF
        jmpr cc_EQ,clear_standalone
        cmpb RL5,#0
        jmpr cc_EQ,rpm_gate

        cmpb RL5,#1
        jmpr cc_NE,pin81
        movb RL5,0xFD51              ; pin 80 = fd50.9
        andb RL5,#0x02
        jmpr cc_EQ,clear_standalone
        jmpr cc_UC,rpm_gate
pin81:  cmpb RL5,#2
        jmpr cc_NE,pin82
        movb RL5,0xFD51              ; pin 81 = fd50.8
        andb RL5,#0x01
        jmpr cc_EQ,clear_standalone
        jmpr cc_UC,rpm_gate
pin82:  cmpb RL5,#4
        jmpr cc_NE,clear_standalone
        movb RL5,0xFD50              ; pin 82 = fd50.7
        andb RL5,#0x80
        jmpr cc_EQ,clear_standalone

rpm_gate:
        movb RL5,0xD801
        andb RL5,#0x01
        jmpr cc_EQ,enter_compare

        movb RL5,0x3012              ; CUT_HYST, RPM/32
        cmpb RL5,#0xFF
        jmpr cc_EQ,zero_hyst
        movb RL6,0x3011              ; CUTRPM
        cmpb RL6,RL5
        jmpr cc_C,zero_hyst
        jmpr cc_EQ,zero_hyst
        subb RL6,RL5
        jmpr cc_UC,release_compare
zero_hyst:
        movb RL6,0x3011
        jmpr cc_UC,release_compare
release_compare:
        movb RL5,0xFAE6              ; actual RPM/32
        cmpb RL5,RL6
        jmpr cc_C,clear_standalone
        jmpr cc_UC,set_standalone

enter_compare:
        movb RL5,0xFAE6
        cmpb RL5,0x3011
        jmpr cc_C,clear_standalone

set_standalone:
        movb RL5,#0x01
        orb  0xD801,RL5
        jmpr cc_UC,fixed_ipw
clear_standalone:
        movb RL5,#0xFE
        andb 0xD801,RL5

fixed_ipw:
        movb RL5,0xD801
        andb RL5,#0x07
        jmpr cc_EQ,freshness_done
        movb RL5,#3
        movb 0xD802,RL5
freshness_done:
        pop r6
        pop r5
        pop DPP0
        mov r4,0xECBA
        rets
