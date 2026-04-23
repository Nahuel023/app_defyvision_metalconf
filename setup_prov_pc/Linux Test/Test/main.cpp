#include <iostream>
#include "Smooth.h"
using namespace std;

int main()
{
    if(!SmoothConnectDriver())
    {
        printf("plase use root!");
    }

    int a0 = SmoothEasyGetPort(0xa02,0); //GPIO 0
    int a1 = SmoothEasyGetPort(0xa02,1);
    int a2 = SmoothEasyGetPort(0xa02,2);
    int a3 = SmoothEasyGetPort(0xa02,3);
    int a4 = SmoothEasyGetPort(0xa02,4);
    int a5 = SmoothEasyGetPort(0xa02,5);
    int a6 = SmoothEasyGetPort(0xa02,6);
    int a7 = SmoothEasyGetPort(0xa02,7);
    printf("value:%d,%d,%d,%d,%d,%d,%d,%d!\r\n",a0,a1,a2,a3,a4,a5,a6,a7);
    return 0;
}
