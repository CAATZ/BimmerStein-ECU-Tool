; Persistent Soft-BSL 0x5A loader: polled ASC0 receive helper.
; File 0x4412..0x4425, CPU 0x0412..0x0425.
base 0x0412

s_rx:   bset 0xFFB0.4
        bclr 0xFF6E.7
sr_w:   srvwdt
        jnb 0xFF6E.7,sr_w
        movb RL4,0xFEB2
        bclr 0xFF6E.7
        rets
