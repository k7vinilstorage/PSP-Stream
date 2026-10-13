	.set noreorder

#include "pspstub.s"

	STUB_START "PSPStreamUSB",0x40090000,0x00080005
	STUB_FUNC  0x87AC184C,pspusb_start
	STUB_FUNC  0x8F2B8A42,pspusb_stop
	STUB_FUNC  0x08F90B7A,pspusb_is_attached
	STUB_FUNC  0x20697DA8,pspusb_wait_attached
	STUB_FUNC  0x0040FF9B,pspusb_write
	STUB_FUNC  0xF3516427,pspusb_read
	STUB_FUNC  0x22640DB3,pspusb_abort
	STUB_FUNC  0x0F89984F,pspusb_clear_buffers
	STUB_END
