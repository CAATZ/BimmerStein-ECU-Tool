; Launch Control V12 - MS41.3 fixed-angle fuel episode owner.
; E849 C0=initialized inactive, C1=held episode, C3=qualified timing override.
; E847 cut requests/freshness and the native fuel stage remain untouched.
; The actual input must remain asserted even though the limiter latch persists.
; UPDATE is owned by late timing. CHECK is read-only for combustion monitors.
; CHECK returns RL4=C3 only for a currently published, freshly eligible override.
; All other registers, DPP0 and PSW are restored, including native caller flags.
base 0x3E800
        push PSW
        push r7
        mov  r7,#0
        jmpr cc_UC,common
        nop                         ; fixed CHECK entry at UPDATE + 0x0A
check:
        push PSW
        push r7
        mov  r7,#1
common:
        push DPP0
        push r4
        push r5
        push r6
        mov  DPP0,#4
        movb RL6,0xE849
        movb RH6,#0xC0              ; compute next state without exposing a false release

        movb RL4,0xE847
        andb RL4,#0xF8
        cmpb RL4,#0xA0              ; initialized and no native-priority episode
        jmpr cc_NE,done
        movb RL4,0x47ED     ; LC_IGN_ANGLE: 0..63, FF off
        cmpb RL4,#63
        jmpr cc_UGT,done            ; invalid bytes cannot command advance
        jnb  0xFDB6.6,done
        movb RL4,0x47E1
        cmpb RL4,#0
        jmpr cc_NE,done             ; fuel mode only
        movb RL4,0xF19A
        cmpb RL4,0x47E5
        jmpr cc_NC,done
        movb RL4,0xE8D0
        cmpb RL4,0x47E6
        jmpr cc_C,done

        movb RL4,0x47E0
        cmpb RL4,#0
        jmpr cc_EQ,threshold        ; Always is a virtual asserted input
        cmpb RL4,#1
        jmpr cc_NE,pin81
        movb RL4,0xFD61
        andb RL4,#0x02
        jmpr cc_UC,polarity
pin81: cmpb RL4,#2
        jmpr cc_NE,pin82
        movb RL4,0xFD61
        andb RL4,#0x01
        jmpr cc_UC,polarity
pin82: cmpb RL4,#4
        jmpr cc_NE,done             ; Off and invalid switch encoding
        movb RL4,0xFD60
        andb RL4,#0x80
polarity:
        movb RL5,0x47E2
        cmpb RL5,#0
        jmpr cc_EQ,active_high
        cmpb RL4,#0
        jmpr cc_NE,done
        jmpr cc_UC,threshold
active_high:
        cmpb RL4,#0
        jmpr cc_EQ,done

threshold:
        movb RL5,0xFC3C
        cmpb RL5,0xDB87
        jmpr cc_NC,done             ; recheck the native hard ceiling between limiter tasks
        movb RL4,0x47E3      ; launch soft owns the eligible effective threshold
        cmpb RL4,0xF014
        jmpr cc_UGT,done            ; a lower native bound takes priority
        jmpr cc_EQ,hard_select
        cmpb RL5,0xF014             ; native task briefly republishes its higher soft bound
        jmpr cc_NC,done             ; reaching that native bound also takes priority
hard_select:
        movb RL5,0x47E7
        cmpb RL5,#0xFF
        jmpr cc_NE,hard_cap
        movb RL5,0x47E3
        cmpb RL5,#0xFD
        jmpr cc_C,auto_add
        movb RL5,#0xFF
        jmpr cc_UC,hard_cap
auto_add:
        addb RL5,#3                 ; automatic hard: launch soft + 96 RPM, saturated
hard_cap:
        cmpb RL5,0xDB87
        jmpr cc_C,first_threshold
        movb RL5,0xDB87
first_threshold:
        cmpb RL5,RL4
        jmpr cc_NC,rpm_compare
        movb RL4,RL5                ; hard below soft starts the same episode
rpm_compare:
        movb RL5,0xFC3C
        cmpb RL5,RL4
        jmpr cc_NC,hold
        andb RL6,#0xFD
        cmpb RL6,#0xC1
        jmpr cc_NE,done             ; stock fuel cutting alone cannot start retard
        jnb  0xFD12.15,done          ; below entry and all fuel returned: episode ends
hold:
        movb RH6,#0xC1
done:
        calls 0x3E740       ; pure qualification returns prospective RH6
        cmpb RL7,#0
        jmpr cc_NE,read_only
        movb 0xE849,RH6             ; only the timing owner publishes state
        jmpr cc_UC,restore
read_only:
        movb RH7,#0xC0
        cmpb RH6,#0xC3
        jmpr cc_NE,restore
        movb RL4,0xE849             ; nested timing release cannot be resurrected
        cmpb RL4,#0xC3
        jmpr cc_NE,restore
        movb RH7,#0xC3
restore:
        pop  r6
        pop  r5
        pop  r4
        pop  DPP0
        cmpb RL7,#1
        jmpr cc_NE,finish
        movb RL4,RH7                ; CHECK's sole register result
finish:
        pop  r7
        pop  PSW
        rets
