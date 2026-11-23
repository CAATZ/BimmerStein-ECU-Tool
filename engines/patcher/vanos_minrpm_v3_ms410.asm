; MS41.0 1429861. The native FD14.2 inhibit precedes the splice.
base 0x34000

        mov  DPP0,#4
        jb   0xFD14.4,closed_throttle
        jnb  0xFD14.5,stock_maps
closed_throttle:
        cmpb 0xFF1E,0x3000     ; ONES versus VANOSRPM, 0xFF selects stock.
        jmpr cc_EQ,stock_release
        movb RL4,0xE9E2        ; Retained RPM in 32 RPM steps, as in V1/V2.
        cmpb RL4,0x3000
        jmpr cc_NC,stock_maps
stock_release:
        jmps 0x02F802
stock_maps:
        jmps 0x02F80A
