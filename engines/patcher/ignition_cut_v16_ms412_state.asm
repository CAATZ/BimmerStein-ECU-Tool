; ignition_cut V16 ms412: runtime RAM D801-D804.
; Derived from ignition_cut_v14_ms412_state.asm; only state operands are relocated.
; Ignition Cut V14 MS41.2 shared late timing owner.
; D803 C0=inactive, C1=eligible episode with protection veto, C3=actual timing.
; D804 source: 0=none, 1=fuel launch, 2=ignition launch, 3=standalone band.
; UPDATE returns RL4=absolute angle byte 0..63, or FF for native timing.
; CHECK (+0A) returns RL4=C3 only for freshly eligible published timing; read-only.
; All other registers, DPP0 and PSW are preserved. D801 is never written here.
; Bands are lower-inclusive/upper-exclusive and never retain a cut request.
base 0x3EA00
        push PSW
        push r7
        mov r7,#0
        jmpr cc_UC,common
        nop
check:
        push PSW
        push r7
        mov r7,#1
common:
        push DPP0
        push r4
        push r5
        push r6
        push r3
        mov DPP0,#4
        movb RL6,0xD803
        movb RH6,#0xC0
        mov r3,#0x00FF
        movb RL4,0xD801
        andb RL4,#0xF8
        cmpb RL4,#0xA0
        jmpa cc_NE,done
        movb RL5,0xFC3C
        cmpb RL5,0xDB87
        jmpa cc_NC,done

        ; A real installed Launch hook is required before reading its latch.
        mov DPP0,#8
        mov r4,0x07E8
        cmp r4,#0x03FA
        jmpa cc_NE,no_launch
        mov r4,0x07EA
        cmp r4,#0xDD00
        jmpa cc_NE,no_launch
        mov DPP0,#4
        movb RL4,0xFDB6
        andb RL4,#0x40
        jmpa cc_EQ,standalone
        movb RL4,0xF19A
        cmpb RL4,0x3531
        jmpa cc_NC,standalone
        movb RL4,0xE8D0
        cmpb RL4,0x3532
        jmpa cc_C,standalone
        movb RL4,0x352C
        cmpb RL4,#0
        jmpa cc_EQ,launch_mode
        cmpb RL4,#1
        jmpa cc_NE,lc_pin81
        movb RL4,0xFD61
        andb RL4,#2
        jmpa cc_UC,polarity
lc_pin81:
        cmpb RL4,#2
        jmpa cc_NE,lc_pin82
        movb RL4,0xFD61
        andb RL4,#1
        jmpa cc_UC,polarity
lc_pin82:
        cmpb RL4,#4
        jmpa cc_NE,standalone
        movb RL4,0xFD60
        andb RL4,#0x80
polarity:
        movb RL5,0x352E
        cmpb RL5,#0
        jmpa cc_EQ,active_high
        cmpb RL5,#1
        jmpa cc_NE,standalone
        cmpb RL4,#0
        jmpa cc_NE,standalone
        jmpa cc_UC,launch_mode
active_high:
        cmpb RL4,#0
        jmpa cc_EQ,standalone
launch_mode:
        movb RL4,0x352D
        cmpb RL4,#0
        jmpa cc_EQ,fuel
        cmpb RL4,#1
        jmpa cc_NE,standalone
        movb RL3,0x353B
        cmpb RL3,#63
        jmpa cc_UGT,standalone
        movb RL4,0x352F
        movb RL5,0x353A
        movb RH3,#2
        jmpa cc_UC,band
fuel:
        movb RL3,0x3539
        cmpb RL3,#63
        jmpa cc_UGT,standalone
        movb RL5,0xFC3C
        movb RL4,0x352F
        cmpb RL4,0xF014
        jmpa cc_UGT,standalone
        jmpa cc_EQ,hard_select
        cmpb RL5,0xF014
        jmpa cc_NC,standalone
hard_select:
        movb RL5,0x3533
        cmpb RL5,#0xFF
        jmpa cc_NE,hard_cap
        movb RL5,0x352F
        cmpb RL5,#0xFD
        jmpa cc_C,auto_add
        movb RL5,#0xFF
        jmpa cc_UC,hard_cap
auto_add:
        addb RL5,#3
hard_cap:
        cmpb RL5,0xDB87
        jmpa cc_C,first_threshold
        movb RL5,0xDB87
first_threshold:
        cmpb RL5,RL4
        jmpa cc_NC,fuel_rpm
        movb RL4,RL5
fuel_rpm:
        movb RL5,0xFC3C
        cmpb RL5,RL4
        jmpa cc_NC,fuel_hold
        andb RL6,#0xFD
        cmpb RL6,#0xC1
        jmpa cc_NE,standalone
        movb RL4,0xD804
        cmpb RL4,#1
        jmpa cc_NE,standalone
        movb RL4,0xFD13
        andb RL4,#0x80
        jmpa cc_EQ,standalone
fuel_hold:
        movb RH3,#1
        jmpa cc_UC,eligible
no_launch:
        mov DPP0,#4
standalone:
        mov r3,#0x00FF
        movb RL4,0x2A65
        cmpb RL4,#0
        jmpa cc_EQ,cut_band
        cmpb RL4,#1
        jmpa cc_NE,cut_pin81
        movb RL4,0xFD61
        andb RL4,#2
        jmpa cc_EQ,done
        jmpa cc_UC,cut_band
cut_pin81:
        cmpb RL4,#2
        jmpa cc_NE,cut_pin82
        movb RL4,0xFD61
        andb RL4,#1
        jmpa cc_EQ,done
        jmpa cc_UC,cut_band
cut_pin82:
        cmpb RL4,#4
        jmpa cc_NE,done
        movb RL4,0xFD60
        andb RL4,#0x80
        jmpa cc_EQ,done
cut_band:
        movb RL3,0x2A6B
        cmpb RL3,#63
        jmpa cc_UGT,done
        movb RL4,0x2A66
        movb RL5,0x2A6A
        movb RH3,#3
band:
        cmpb RL5,#0
        jmpa cc_EQ,band_miss
        cmpb RL5,RL4
        jmpa cc_NC,band_miss
        cmpb RL4,0xFC3C
        jmpa cc_ULE,band_miss
        subb RL4,RL5
        movb RL5,0xFC3C
        cmpb RL5,RL4
        jmpa cc_C,band_miss
        cmpb RL5,0xF014
        jmpa cc_NC,band_miss
eligible:
        movb RH6,#0xC1
        jmpa cc_UC,done
band_miss:
        cmpb RH3,#2
        jmpa cc_EQ,standalone
        mov r3,#0x00FF
done:
        calls 0x3E740
        cmpb RL7,#1
        jmpa cc_EQ,read_only
        movb RH7,#0xFF
        cmpb RH6,#0xC3
        jmpa cc_NE,publish
        movb RH7,RL3
publish:
        ; Publish source and eligibility together; never expose a false release.
        bclr 0xFF10.11
        movb 0xD804,RH3
        movb 0xD803,RH6
        jmpa cc_UC,restore
read_only:
        movb RH7,#0xC0
        cmpb RH6,#0xC3
        jmpa cc_NE,restore
        movb RL4,0xD803
        cmpb RL4,#0xC3
        jmpa cc_NE,restore
        movb RL4,0xD804
        cmpb RL4,RH3
        jmpa cc_NE,restore
        movb RH7,#0xC3
restore:
        pop r3
        pop r6
        pop r5
        pop r4
        pop DPP0
        movb RL4,RH7
        pop r7
        pop PSW
        rets
