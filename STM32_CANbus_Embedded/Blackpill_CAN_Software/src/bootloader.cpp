#include "bootloader.h"

#include <Arduino.h>

static constexpr uint32_t DFU_MAGIC = 0xB007DF00u;
static constexpr uint32_t SYSTEM_MEMORY = 0x1FFF0000u;   // STM32F411 system bootloader

static void backupAccess() {
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    PWR->CR |= PWR_CR_DBP;
}

void rebootToDfu() {
    backupAccess();
    RTC->BKP0R = DFU_MAGIC;
    delay(20);   // let the console flush its last line
    NVIC_SystemReset();
}

static void jumpToSystemBootloader() {
    __disable_irq();
    SysTick->CTRL = 0;
    SysTick->LOAD = 0;
    SysTick->VAL = 0;
    for (uint32_t i = 0; i < 8; i++) {
        NVIC->ICER[i] = 0xFFFFFFFFu;
        NVIC->ICPR[i] = 0xFFFFFFFFu;
    }
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;
    (void)RCC->APB2ENR;
    SYSCFG->MEMRMP = SYSCFG_MEMRMP_MEM_MODE_0;   // system flash mapped at 0x00000000
    __DSB();
    __ISB();
    const uint32_t sp = *reinterpret_cast<volatile uint32_t *>(SYSTEM_MEMORY);
    const uint32_t entry = *reinterpret_cast<volatile uint32_t *>(SYSTEM_MEMORY + 4);
    __set_MSP(sp);
    __enable_irq();   // nothing is enabled or pending; the bootloader expects PRIMASK clear
    reinterpret_cast<void (*)()>(entry)();
    while (true) {}
}

extern "C" void hw_config_init(void);

// Replaces the core's weak init() (cores/arduino/board.c), which only calls hw_config_init().
extern "C" void init(void) {
    backupAccess();
    if (RTC->BKP0R == DFU_MAGIC) {
        RTC->BKP0R = 0;
        jumpToSystemBootloader();
    }
    hw_config_init();
}
