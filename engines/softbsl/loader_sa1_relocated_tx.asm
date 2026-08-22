; Persistent Soft-BSL 0x5A loader: polled ASC0 transmit helper.
; File 0x5CA0..0x5CB1, CPU 0x1CA0..0x1CB1.
base 0x1CA0

s_tx:   bclr 0xFFB0.4
        movb 0xFEB0,RL4
st_w:   srvwdt
        jnb 0xFF6C.7,st_w
        bclr 0xFF6C.7
        rets
