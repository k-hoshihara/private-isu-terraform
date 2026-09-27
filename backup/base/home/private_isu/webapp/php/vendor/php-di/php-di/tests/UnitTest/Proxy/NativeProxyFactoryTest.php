<?php

declare(strict_types=1);

namespace DI\Test\UnitTest\Proxy;

use DI\Proxy\NativeProxyFactory;
use DI\Test\UnitTest\Proxy\Fixtures\ClassToProxy;
use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\Attributes\RequiresPhp;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * @covers \DI\Proxy\NativeProxyFactory
 * @requires PHP 8.4
 */
#[CoversClass(\DI\Proxy\NativeProxyFactory::class)]
class NativeProxyFactoryTest extends TestCase
{
    /**
     * @test
     */
    #[Test]
    #[RequiresPhp('>= 8.4')]
    public function should_create_native_lazy_proxies()
    {
        $factory = new NativeProxyFactory;

        $instance = new ClassToProxy();
        $initialized = false;

        $initializer = function () use ($instance, &$initialized) {
            $initialized = true;
            return $instance;
        };

        /** @var ClassToProxy $proxy */
        $proxy = $factory->createProxy(ClassToProxy::class, $initializer);

        $this->assertFalse($initialized);
        $this->assertInstanceOf(ClassToProxy::class, $proxy);

        $proxy->foo();

        $this->assertTrue($initialized);
    }
}
