//
// Created by l on 22-6-23.
//

#ifndef STABILITY_SMOOTH_H
#define STABILITY_SMOOTH_H

#ifdef __cplusplus
extern "C" {
#endif // __cplusplus

#include <stdbool.h>

#define SIO_CONFIG_INDEX    0x4E
#define SIO_CONFIG_DATA     0x4F

/**
 * 所有的函数都需要此函数返回 true 后才可以调用.
 * @return bool
 */
bool SmoothConnectDriver();

/**
 * 简易方法,通过地址和位直接读取对应GPIO数据
 * @param {unsigned short} nAddress 地址
 * @param {unsigned char} nOffset 位
 * @return {int} 0 为低电平,1 为高电平,其他为错误
 */
int SmoothEasyGetPort(unsigned short nAddress, unsigned char nOffset);

/**
 * 简易方法,通过地址和位直接设置对应GPIO数据
 * @param {unsigned short} nAddress 地址
 * @param {unsigned char} nOffset 位
 * @param {unsigned char} value 值, 只能为 0 或者 1
 * @return {bool}
 */
bool SmoothEasySetPort(unsigned short nAddress,unsigned char nOffset,unsigned char nValue);


/**
 * 高级函数(请勿随意调用)
 * @param ucAddr
 * @param ucVal
 * @return  bool
 */
bool SmoothIoRead(unsigned char ucAddr, unsigned char *ucVal);
/**
 * 高级函数(请勿随意调用)
 * @param ucAddr
 * @param ucVal
 * @return bool
 */
bool SmoothIoWrite(unsigned char ucAddr, unsigned char ucVal);
#ifdef __cplusplus
}
#endif // __cplusplus
#endif //STABILITY_SMOOTH_H
