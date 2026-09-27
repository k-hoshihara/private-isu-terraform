<?php

declare(strict_types=1);

namespace DI\Test\UnitTest\Proxy;

use DI\Proxy\ProxyFactory;
use DI\Test\UnitTest\Proxy\Fixtures\ClassToProxy;
use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\Attributes\RequiresPhp;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * @covers \DI\Proxy\ProxyFactory
 */
#[CoversClass(\DI\Proxy\ProxyFactory::class)]
class ProxyFactoryTest extends TestCase
{
    /**
     * @test
     */
    #[Test]
    #[RequiresPhp('< 8.4')]
    public function should_create_lazy_proxies()
    {
        $factory = new ProxyFactory;

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
        $this->assertSame($instance, $proxy->getInstance());
    }
}
