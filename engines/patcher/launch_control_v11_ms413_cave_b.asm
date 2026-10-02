; MS41.3 Launch V11: independent soft threshold and native A/B priority.
; E847.3 is a selector, never a cut request. FD12.15 owns episode lifetime.
; Invalid runtime marker leaves native soft/state untouched; A/B helpers use stock.
base 0x3DD00
        push DPP0
        push r4
        push r5
        mov  DPP0,#4
        movb RL4,0xE847
        andb RL4,#0xF0
        cmpb RL4,#0xA0
        jmpr cc_NE,done
        jb   0xFD12.15,eligibility
        movb RL4,#0xF7
        andb 0xE847,RL4               ; new native episode clears stale priority
eligibility:
        jnb  0xFDB6.6,not_fuel
        movb RL4,0x47E1
        cmpb RL4,#0
        jmpr cc_NE,not_fuel
        movb RL4,0xF014    ; completed native soft selection, including fault cap
        cmpb RL4,0x47E3
        jmpr cc_ULE,stock_priority    ; native bound or tie owns hysteresis
        movb RL5,0xFC3C
        cmpb RL5,RL4
        jmpr cc_NC,stock_priority
        cmpb RL5,0xDB87
        jmpr cc_NC,stock_priority
        jmpr cc_UC,apply_soft
stock_priority:
        movb RL5,#0x08
        orb  0xE847,RL5
apply_soft:
        movb RL5,0x47E3
        cmpb RL5,RL4
        jmpr cc_NC,done
        movb 0xF014,RL5    ; min(native soft, launch soft); hard never lowers soft
        jmpr cc_UC,done
not_fuel:
        jnb  0xFD12.15,done
        movb RL5,#0x08
        orb  0xE847,RL5               ; disarm/rearm cannot change active recovery to custom
done:
        pop  r5
        pop  r4
        pop  DPP0
        jb   0xFD12.15,active         ; displaced native dispatch, no stage/counter edits
        jmps 0x020926
active:
        jmps 0x0207F0
