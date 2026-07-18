; Persistent Soft-BSL 0x5A loader: polled ASC0 helpers.
; File 0x5FC4..0x5FE9, CPU 0x1FC4..0x1FE9.
base 0x1FC4

s_rx:   bset 0xFFB0.4
        bclr 0xFF6E.7
sr_w:   srvwdt
        jnb 0xFF6E.7,sr_w
        movb RL4,0xFEB2
        bclr 0xFF6E.7
        rets
s_tx:   bclr 0xFFB0.4
        movb 0xFEB0,RL4
st_w:   srvwdt
        jnb 0xFF6C.7,st_w
        bclr 0xFF6C.7
        rets
