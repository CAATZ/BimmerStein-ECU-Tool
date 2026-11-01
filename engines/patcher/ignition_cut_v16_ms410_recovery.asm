; ignition_cut V16 ms410: runtime RAM D801-D804.
; Derived from ignition_cut_v11_ms410_recovery.asm; only state operands are relocated.
; Ignition Cut V11 ms410 accepted samples and catalyst window.
; D801 owns cut requests; D802 owns post-cut accepted-sample freshness.
; Every hook preserves the native R0 stack ABI and genuine fault fallback.
base 0x33000

; CALLS replaces either native accepted front-sample store.
; Briefly serialize published cut/retard check with the pending-bit clear.
; An IRQ after PSW restore reasserts pending before downstream observation can use the sample.
front_sample:
        push PSW
        bclr 0xFF10.11
        push r4
        calls 0x32F50
        jmpr cc_NE,front_sample_done
        movb RL4,#254
        cmp r9,#0xED56
        jmpr cc_EQ,front_sample_clear
        movb RL4,#253
front_sample_clear:
        andb 0xD802,RL4
        pop r4
        pop PSW
        movb [r9+#0x2B],RL6
        rets
front_sample_done:
        pop r4
        pop PSW
        rets
