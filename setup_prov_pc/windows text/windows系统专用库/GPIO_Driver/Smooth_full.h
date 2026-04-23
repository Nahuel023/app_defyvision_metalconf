
#ifndef SMOOTH_H
#define SMOOTH_H

extern "C"
{

typedef enum EVENT_MODE {
    FALLING_EDGE = 0,         //下降沿
    RISING_EDGE = 1,          //上升沿
    BOTH_EDGE = 4             //跳变
} EVENT_MODE;

typedef void (*EventCallback)(EVENT_MODE, int);


/**
 * 安装驱动,此函数必须要以管理员权限运行.
 * 建议单独编写安装程序 调用此参数将驱动安装到系统内,供软件静态调用.
 * @param IsDemandLoaded 参数必须为 false
 * @return
 */
bool __stdcall InstallSmoothDriver(bool IsDemandLoaded = false);

/**
 * 注意除 SmoothReadHardwareInfo 外,其所有的函数都需要此函数返回 true 后才可以调用
 * 静态/动态连接驱动,当驱动已经安装到系统时候,
 * 则为静态连接,静态连接驱动不需要调用 SmoothDisconnectDriver 释放驱动.
 * 当驱动未安装到系统时,则为动态调用,动态调用时,在需要以管理员权限调用此接口.
 * 同时在程序退出时,需要调用 SmoothDisconnectDriver
 * 建议 先把驱动安装进系统,然后 使用 Smooth
 * @return bool
 */
bool __stdcall SmoothConnectDriver();
/**
 * 断开驱动连接,动态连接方法使用
 */
void __stdcall SmoothDisconnectDriver();
/**
 * 从配置文件内自动初始化,配置文件应当存放在程序同目录下,且文件名必须为Config.ini
 * @return bool
 */
bool __stdcall SmoothAutoInitConfigFromFile();

/**
 * 自动初始化
 * @param lpszDevClass GUID 当此参数为 空 或者 为 AUTO 时,将会从BIOS 内读取 GUID 来自动初始化,也可以直接传递设备GUID,设备GUID 可以在
 * <Smooth开发指南.pdf> 查看
 * @return bool
 */
bool __stdcall SmoothAutoInitConfig(LPCWSTR lpszDevClass);
/**
 * 使能对应输入 PIN 脚 指定事件, 目前支持 三种事件 具体 查看 EVENT_MODE 定义
 * 此函数需要 通过 SmoothAutoInitConfigFromFile 或者 SmoothAutoInitConfig 初始化成功后才可以使用
 * @param pstEventMode
 * @param wIndexOfPin PIN 脚序号 从 1 开始
 * @return bool
 */
bool __stdcall SmoothEnableInputEvent(EVENT_MODE pstEventMode, int wIndexOfPin);
/**
 * 停用指定 PIN 脚 的指定事件
 * 此函数需要 通过 SmoothAutoInitConfigFromFile 或者 SmoothAutoInitConfig 初始化成功后才可以使用
 * @param pstEventMode
 * @param wIndexOfPin
 * @return bool
 */

bool __stdcall SmoothDisableInputEvent(EVENT_MODE pstEventMode, int wIndexOfPin);

/**
 * 注册 EVENT_MODE 回调事件
 * @param eventCallBack
 * @return bool
 */
bool _stdcall SmoothRegisterInputEventCallBack(EventCallback eventCallBack);

/**
 * 设置 指定名称的输出状态,
 * 此函数只能在 通过  SmoothAutoInitConfigFromFile 初始化成功后使用.
 * @param lpAppName 节名称,其名称可以打开程序目录的Config.ini 查看 一般为 GPOx
 * @param pdwPinVal true/false,当函数返回 true 时,此值才有实际意义.
 * @return bool
 */
bool __stdcall SmoothSetPortOrPhyValue(LPCWSTR lpAppName, bool pdwPinVal);
/**
 * 读取 指定名称的输入状态,
 * 此函数只能在 通过  SmoothAutoInitConfigFromFile 初始化成功后使用.
 * @param lpAppName 节名称,其名称可以打开程序目录的Config.ini 查看 一般为 GPIx
 * @return int 1 为高电平,0 为低电平, 其他为错误
 */
int __stdcall SmoothGetPortOrPhyValue(LPCWSTR lpAppName);

/**
 * 读取 指定端口的输入状态
 * 此函数需要 通过 SmoothAutoInitConfigFromFile 或者 SmoothAutoInitConfig 初始化成功后才可以使用
 * @param wIndexOfPin PIN 脚序号 从 1 开始
 * @param pdwPinVal true/false , 当函数返回 true 时,此值才有实际意义.
 * @return bool
 */
bool __stdcall SmoothReadInput(int wIndexOfPin, bool *pdwPinVal);

/**
 * 设置 指定端口的输出状态
 * 此函数需要 通过 SmoothAutoInitConfigFromFile 或者 SmoothAutoInitConfig 初始化成功后才可以使用
 * @param wIndexOfPin  PIN 脚序号 从 1 开始
 * @param pdwPinVal  true/false , 当函数返回 true 时,此值才有实际意义.
 * @return true/false , 当函数返回 true 时,此值才有实际意义.
 */
bool __stdcall SmoothWriteOutput(int wIndexOfPin, bool pdwPinVal);

/**
 * 读取寄存器的值
 * @param wPortAddr
 * @param pdwPortVal
 * @param bSize
 * @return bool
 */
bool __stdcall SmoothReadPortVal(WORD wPortAddr, PDWORD pdwPortVal, BYTE bSize);

/**
 * 设置指定寄存器的值
 * @param wPortAddr
 * @param dwPortVal
 * @param bSize
 * @return bool
 */
bool __stdcall SmoothWritePortVal(WORD wPortAddr, DWORD dwPortVal, BYTE bSize);

/**
 * 简易方法,通过地址和位直接读取对应GPIO数据
 * @param {unsigned short} nAddress 地址
 * @param {unsigned char} nOffset 位
 * @return {int} 0 为低电平,1 为高电平,其他为错误
 */
int _stdcall SmoothEasyGetPort(unsigned short nAddress, unsigned char nOffset);

/**
 * 简易方法,通过地址和位直接设置对应GPIO数据
 * @param {unsigned short} nAddress 地址
 * @param {unsigned char} nOffset 位
 * @param {unsigned char} value 值, 只能为 0 或者 1
 * @return {bool}
 */
bool _stdcall SmoothEasySetPort(unsigned short nAddress,unsigned char nOffset,unsigned char nValue);

/**
 * 读取部分 DMI 信息
 * @param pwszBoardManufacturer
 * @param pwszBoardProductName
 * @param pwszSystemManufacturer
 * @param pwszSystemProductName
 * @param pwszSystemSerialNumber
 * @param pwszSystemUUID
 * @param pwszFirmwareReleaseDate
 * @return bool
 */
bool __stdcall SmoothReadHardwareInfo(PWCHAR pwszBoardManufacturer, PWCHAR pwszBoardProductName, PWCHAR pwszSystemManufacturer,
                            PWCHAR pwszSystemProductName, PWCHAR pwszSystemSerialNumber, PWCHAR pwszSystemUUID,
                            PWCHAR pwszFirmwareReleaseDate);


/**
 * 高级函数(请勿随意调用)
 * @param ucAddr
 * @param ucData
 */
void __stdcall SmoothSetAddressAndDataPort(unsigned char ucAddr, unsigned char ucData);
/**
 * 高级函数(请勿随意调用)
 * @param ucAddr
 * @param ucVal
 * @return  bool
 */
bool __stdcall SmoothIoRead(unsigned char ucAddr, unsigned char *ucVal);
/**
 * 高级函数(请勿随意调用)
 * @param ucAddr
 * @param ucVal
 * @return
 */
bool __stdcall SmoothIoWrite(unsigned char ucAddr, unsigned char ucVal);

/**
 * 高级函数(请勿随意调用)
 * @return bool
 */
bool __stdcall RemoveSmoothDriver();



struct tagPhysStruct {
    DWORD64 dwPhysMemSizeInBytes;
    DWORD64 pvPhysAddress;
    DWORD64 PhysicalMemoryHandle;
    DWORD64 pvPhysMemLin;
    DWORD64 pvPhysSection;
};

/**
 * 高级函数
 * @param PhysStruct
 * @return bool
 */
PBYTE __stdcall MapPhysToLin(tagPhysStruct &PhysStruct);
/**
 * 高级函数
 * @param PhysStruct
 * @return bool
 */
bool __stdcall UnmapPhysicalMemory(tagPhysStruct &PhysStruct);
/**
 * 高级函数
 * @param pbPhysAddr
 * @param pdwPhysVal
 * @return bool
 */
bool __stdcall GetPhysLong(PBYTE pbPhysAddr, PDWORD pdwPhysVal);
/**
 * 高级函数
 * @param pbPhysAddr
 * @param dwPhysVal
 * @return bool
 */
bool __stdcall SetPhysLong(PBYTE pbPhysAddr, DWORD dwPhysVal);

}

#endif
