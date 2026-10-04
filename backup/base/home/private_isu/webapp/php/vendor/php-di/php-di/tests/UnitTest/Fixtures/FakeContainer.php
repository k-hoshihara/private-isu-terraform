<?php

declare(strict_types=1);

namespace DI\Test\UnitTest\Fixtures;

use DI\Definition\Source\DefinitionSource;
use DI\Proxy\ProxyFactory;
use DI\Proxy\ProxyFactoryInterface;
use Psr\Container\ContainerInterface;

/**
 * Fake container class that exposes all constructor parameters.
 *
 * Used to test the ContainerBuilder.
 */
class FakeContainer
{
    /**
     * @var DefinitionSource
     */
    public $definitionSource;

    /**
     * @var ProxyFactoryInterface
     */
    public $proxyFactory;

    /**
     * @var ContainerInterface
     */
    public $wrapperContainer;

    public function __construct(
        DefinitionSource $definitionSource,
        ProxyFactoryInterface $proxyFactory,
        ?ContainerInterface $wrapperContainer = null,
    ) {
        $this->definitionSource = $definitionSource;
        $this->proxyFactory = $proxyFactory;
        $this->wrapperContainer = $wrapperContainer;
    }
}
