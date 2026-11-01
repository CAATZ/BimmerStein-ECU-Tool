; V18 request/recovery owner. The following hook calls the bounded fuel owner.
; ignition_cut V16 ms413: runtime RAM D801-D804.
; Derived from ignition_cut_v14_ms413_control.asm; only state operands are relocated.
; Ignition Cut V14 - MS41.3 late IPW consumer and CC6 request helper.
; The native late hook initializes state and publishes fixed IPW only.
; CC6 alone updates standalone D801.0; Launch alone updates D801.1.
base 0x3E600
        push PSW
        bclr 0xFF10.11
        push DPP0
        push r5
        mov DPP0,#4
        calls 0x3DCB0
        movb RL5,0xD801
        andb RL5,#0x07
        jmpr cc_EQ,freshness_done
        movb RL5,#15
        orb 0xD802,RL5
freshness_done:
        pop r5
        pop DPP0
        pop PSW
        mov r4,0xEF7C
        rets
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
; CPU 3E660. Called only at a native coil-charge event after marker validation.
; CUT_HYST=00/FF both release strictly below CUTRPM. Preserve all sibling bits.
request:
        push DPP0
        push r5
        push r6
        mov DPP0,#4
        movb RL5,0x2A65
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
pin81: cmpb RL5,#2
        jmpr cc_NE,pin82
        movb RL5,0xFD61
        andb RL5,#0x01
        jmpr cc_EQ,clear_standalone
        jmpr cc_UC,rpm_gate
pin82: cmpb RL5,#4
        jmpr cc_NE,clear_standalone
        movb RL5,0xFD60
        andb RL5,#0x80
        jmpr cc_EQ,clear_standalone
rpm_gate:
        movb RL5,0xD801
        andb RL5,#0x01
        jmpr cc_EQ,enter_compare
        movb RL5,0x2A67
        cmpb RL5,#0xFF
        jmpr cc_EQ,zero_hyst
        movb RL6,0x2A66
        cmpb RL6,RL5
        jmpr cc_C,zero_hyst
        jmpr cc_EQ,zero_hyst
        subb RL6,RL5
        jmpr cc_UC,release_compare
zero_hyst:
        movb RL6,0x2A66
        jmpr cc_UC,release_compare
release_compare:
        movb RL5,0xFC3C
        cmpb RL5,RL6
        jmpr cc_C,clear_standalone
        jmpr cc_UC,set_standalone
enter_compare:
        movb RL5,0xFC3C
        cmpb RL5,0x2A66
        jmpr cc_C,clear_standalone
set_standalone:
        movb RL5,#1
        orb 0xD801,RL5
        jmpr cc_UC,request_done
clear_standalone:
        movb RL5,#0xFE
        andb 0xD801,RL5
request_done:
        pop r6
        pop r5
        pop DPP0
        rets
