; Ignition Cut V10 ms410 accepted samples and catalyst window.
; E847 owns cut requests; E848 owns post-cut accepted-sample freshness.
; Every hook preserves the native R0 stack ABI and genuine fault fallback.
base 0x33000

; CALLS replaces either native accepted front-sample store.
; During cut skip publication; after release publish and clear only this bank bit.
front_sample:
        push PSW
        push r4
        calls 0x32D20
        jmpr cc_NE,front_sample_done
        movb RL4,#254
        cmp r9,#0xED56
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
