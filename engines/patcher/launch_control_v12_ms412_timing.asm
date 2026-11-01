; MS41.2 Launch V12 absolute fuel-mode ignition angle.
; Both native final angle paths call this after cylinder correction/saturation.
; R2 is the inverted scheduled angle: 72 - raw*0.375 degrees BTDC.
; LC_IGN_ANGLE 0..63 maps -23.625..0 BTDC; FF disables the override.
; Native dwell, charge-start, event buffers and release remain downstream.
base 0x3E700
        push DPP0
        push r5
        calls 0x3E800
        movb RL5,0xE849
        cmpb RL5,#0xC3
        jmpr cc_NE,stock_split
        mov DPP0,#4
        movb RL5,0x3539
        cmpb RL5,#63
        jmpr cc_UGT,stock_split
        mov r2,#255
        subb RL2,RL5
        movb RL5,#0x1F
        orb  0xE848,RL5             ; record every actual override, even between foreground tasks
stock_split:
        pop r5
        pop DPP0
        mov r0,r2
        andb RL0,#0x0F
        rets
