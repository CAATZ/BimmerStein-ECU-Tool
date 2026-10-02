; Ignition Cut V10 ms413 accepted samples and catalyst window.
; E847 owns cut requests; E848 owns post-cut accepted-sample freshness.
; Every hook preserves the native R0 stack ABI and genuine fault fallback.
base 0x3E500

; CALLS replaces either native accepted front-sample store.
; During cut skip publication; after release publish and clear only this bank bit.
front_sample:
        push PSW
        push r4
        calls 0x3E1A0
        jmpr cc_NE,front_sample_done
        movb RL4,#254
        cmp r9,#0xF018
        jmpr cc_EQ,front_sample_clear
        movb RL4,#253
front_sample_clear:
        andb 0xE848,RL4
        pop r4
        pop PSW
        movb [r9+#0x2B],RL6
        rets
front_sample_done:
        pop r4
        pop PSW
        rets

; CALLS replaces either native accepted rear-sample store.
; During cut skip publication; after release publish and clear only this bank bit.
rear_sample:
        push PSW
        push r4
        calls 0x3E1A0
        jmpr cc_NE,rear_sample_done
        movb RL4,#251
        cmp r9,#0xF018
        jmpr cc_EQ,rear_sample_clear
        movb RL4,#247
rear_sample_clear:
        andb 0xE848,RL4
        pop r4
        pop PSW
        movb [r9+#0x42],RL4
        rets
rear_sample_done:
        pop r4
        pop PSW
        rets

; Discard only a cut-contaminated partial observation window.
; A previously completed valid window still reaches native evaluation and DTCs.
catalyst_window:
        push r4
        push r5
        mov r5,r9
        calls 0x3E1E8
        jmpr cc_EQ,catalyst_stock
        mov r4,#0
        movb [r9+#0x40],RL4
        mov [r9+#0x5E],r4
        mov [r9+#0x60],r4
        mov [r9+#0x62],r4
        mov [r9+#0x64],r4
        mov r4,[r9+#0x6E]
        cmp r4,0x68
        jmpr cc_UGT,catalyst_completed
        mov r4,#0
        mov [r9+#0x66],r4
        mov [r9+#0x68],r4
        mov [r9+#0x6A],r4
        mov [r9+#0x6C],r4
        mov [r9+#0x6E],r4
        pop r5
        pop r4
        jmps 0x29476
catalyst_completed:
        pop r5
        pop r4
        jmps 0x293E0
catalyst_stock:
        pop r5
        pop r4
        jb 0xFD10.2,catalyst_eligible
        jmps 0x293DA
catalyst_eligible:
        jmps 0x291EA
