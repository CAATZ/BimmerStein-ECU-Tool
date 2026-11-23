; MS41.1 1437806. The stock FD14.2 inhibit runs before the splice.
base 0x3F87A

        mov  DPP0,#4
        jb   0xFD14.4,closed_throttle
        jnb  0xFD14.5,stock_maps
closed_throttle:
        cmpb 0xFF1E,0x3720     ; ONES versus VANOSRPM; 0xFF selects stock.
        jmpr cc_EQ,stock_release
        movb RL4,0xE9E2        ; Retained RPM in 32 RPM steps, as in V1.
        cmpb RL4,0x3720
        jmpr cc_NC,stock_maps
stock_release:
        jmps 0x03BBC8
stock_maps:
        jmps 0x03BBCE
